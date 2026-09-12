"""MissionController：UI 与 RuntimeAPI 之间的业务封装。

MainWindow 不再知道 MissionSpec/知识路径——只调 controller.start/stop。
第七轮审查（BUG-052~057）：持久化重写——
  BUG-052：完成状态绑定知识包（pkg_key，跨版本不污染）
  BUG-053：单 key 原子保存（version/completed/timestamp 一次写入）
  BUG-054：损坏状态显式报错（不静默降级为空）
  BUG-055：completed 合法性校验（不在当前目标集 → 告警）
  BUG-056：审计失败日志化（不静默）
  BUG-057：地图隔离（key 含 pkg_key，切换不串状态）
"""
import hashlib
import threading
from pathlib import Path

from PySide6.QtCore import QObject

from config.settings import ROOT, data_path

from runtime.api.commands import MissionSpec


class StateCorruptionError(RuntimeError):
    """BUG-054：完成状态损坏——需人工处理（不静默当空）。"""


class MissionController(QObject):
    STATE_VERSION = 4  # A 线 P0（黑塔 1 舱段）：v4 增加 chest_done 表

    # P1-006：必须 RLock——record_completed 内再调 completed_targets() 会二次获取
    _persist_lock = threading.RLock()

    def __init__(self, runtime, knowledge_dir=None, parent=None):
        super().__init__(parent)
        self.runtime = runtime
        # 知识库路径基于仓库根绝对定位（任意 cwd 启动不失效）
        self.knowledge_dir = knowledge_dir or str(
            ROOT / "knowledge" / "source" / "black_tower_test")

    # 持久化（BUG-052/053/057）

    def _pkg_key(self):
        """知识包身份键：路径 hash——切换知识目录即换状态域（BUG-057）。"""
        return hashlib.sha256(self.knowledge_dir.encode()).hexdigest()[:12]

    def _state_key(self):
        return f"mission_state:{self._pkg_key()}"

    def _load_state(self):
        """读取完成状态（委托 runtime.chest_state 做 v3→v4 自动迁移）。

        A 线 P0 黑塔 1 舱段：v4 在 completed 列表外挂 chest_done 表——
        按 chest_id 记录完成时间/rarity/验证时间/房间/工作流。
        """
        import runtime.chest_state as cs
        return cs.load(self._pkg_key())

    def completed_targets(self):
        with self._persist_lock:
            return list(self._load_state().get("completed", []))

    def record_completed(self, target_ids, **meta):
        """单 chest 原子写（v4：同步更新 completed 列表 + chest_done 表）。

        A 线 P0：按 chest_id 落盘。target_ids 为单元素列表时更新 chest_done
        表并记录 meta（rarity/room/workflow/verified_at）。
        """
        import time
        import runtime.chest_state as cs
        with self._persist_lock:
            for tid in target_ids:
                cs.mark_done(
                    self._pkg_key(), tid,
                    rarity=meta.get("rarity", "unknown"),
                    room=meta.get("room"),
                    workflow=meta.get("workflow"),
                    verified_at=meta.get("verified_at", time.time()),
                    note=meta.get("note"),
                )

    def record_done(self, chest_id, **meta):
        """v4 语义化 API：单 chest 标记完成（按 chest_id 落盘）。"""
        import time
        import runtime.chest_state as cs
        with self._persist_lock:
            cs.mark_done(
                self._pkg_key(), chest_id,
                rarity=meta.get("rarity", "unknown"),
                room=meta.get("room"),
                workflow=meta.get("workflow"),
                verified_at=meta.get("verified_at", time.time()),
                note=meta.get("note"),
            )

    def chest_done(self, chest_id):
        """查询单 chest 完成状态（v4）。"""
        state = self._load_state()
        return state.get("chest_done", {}).get(chest_id)

    def all_chests_done(self, expected_ids):
        """按 chest_id 集合判定全部完成（空集合不视为完成——踩坑清单）。"""
        import runtime.chest_state as cs
        return cs.all_done(self._pkg_key(), expected_ids)

    def clear_state(self):
        """清空完成状态（测试/开发用）。"""
        import runtime.chest_state as cs
        with self._persist_lock:
            cs.clear(self._pkg_key())

    def pending_targets(self, all_targets):
        """返回未完成目标（v4：chest_done 表 ∪ completed 列表兼容）。"""
        import runtime.chest_state as cs
        done = set(self.completed_targets())
        # 也查 chest_done 表（v4 权威来源）
        done |= set(self._load_state().get("chest_done", {}).keys())
        # BUG-055：完成记录合法性——不在当前目标集 → 告警（跨版本残留）
        known = set(all_targets)
        unknown = done - known
        if unknown:
            import logging
            logging.getLogger("gui.mission_controller").warning(
                "完成记录含未知目标（知识包版本变化？）: %s", sorted(unknown)[:5])
        return [t for t in all_targets if t not in done]

    def set_map(self, knowledge_dir):
        """地图切换 → 换知识目录（状态域随 pkg_key 自动隔离）。"""
        if knowledge_dir:
            self.knowledge_dir = str(
                ROOT / knowledge_dir if not Path(knowledge_dir).is_absolute()
                else knowledge_dir)

    # 任务控制

    def start(self, targets):
        """GUI 语义化启动（路径/规格封装在 controller 内；真机唯一路径）。"""
        self._audit(f"start mode=real targets={len(targets or [])}")
        spec = MissionSpec(knowledge_dir=self.knowledge_dir,
                           target_ids=targets or None)
        self.runtime.start_mission(spec)

    def stop(self):
        self._audit("stop")
        self.runtime.stop()

    @staticmethod
    def _audit(action):
        """用户操作审计（append-only 日志）。
        BUG-056：写入失败日志化（审计链不可静默断）。"""
        import logging
        import time
        try:
            log_dir = data_path("logs")
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / "user_action.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {action}\n")
        except Exception:
            logging.getLogger("gui.mission_controller").exception(
                "用户操作审计写入失败: %s", action)

    @property
    def state(self):
        return self.runtime.state
