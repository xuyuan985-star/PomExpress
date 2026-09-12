import threading
import time
from dataclasses import dataclass
from typing import Optional

from config.settings import data_path
from runtime.events.bus import EventBus
from runtime.events.schema import make_event


@dataclass
class MissionSpec:
    knowledge_dir: str
    target_ids: Optional[list] = None
    natural_mode: bool = True    # False → 确定性执行（delay=0，可复现测试）
    requires: Optional[list] = None    # S15：任务必需能力（缺则拒绝启动），默认 G3 critical 集


class RuntimeAPI:
    # 合法状态集——非法字符串进不来（UI/执行不进入未知状态）
    # P1-008：与 MissionState 枚举对齐（runtime/state.py 统一状态体系）
    VALID_STATES = {"idle", "running", "done", "crashed", "stopped",
                    "gate_blocked", "paused", "paused_for_human",
                    "resume_check", "invalid"}

    @property
    def mission_state(self):
        """P1-008：统一枚举状态（UI/外部消费用，替代裸字符串）。"""
        from runtime.state import normalize_state
        return normalize_state(self._state)

    def __init__(self, event_bus: EventBus, execution_id=None):
        self.bus = event_bus
        self.execution_id = execution_id
        self._runner = None
        self._thread = None
        self._set_state("idle")
        self._pending_requires = None
        self._stop_event = threading.Event()    # 真停止信号
        self._pause_event = threading.Event()    # F10-011：真暂停信号（runner 在 run_mission 前阻塞）

    def _set_state(self, new_state):
        """状态写入统一入口——非法状态抛 ValueError（防漂移）。"""
        if new_state not in self.VALID_STATES:
            raise ValueError(f"非法状态: {new_state!r}（合法: {sorted(self.VALID_STATES)}）")
        self._state = new_state

    def start_mission(self, spec: MissionSpec, runner_factory=None):
        # 审查：runtime 层防重入（UI 防重入是前端，这里双保险）
        # 旧 runner 线程未退出时启动新线程会双执行（paused/stopped 收尾期）
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError(
                f"任务仍在运行（state={self._state}）——请先停止再启动")
        self._pending_requires = spec.requires
        self._stop_event.clear()    #
        self._pause_event.clear()    # F10-011：新任务清除旧暂停状态

        def runner(bus, execution_id):
            # DS 审计 V3 解环：canonical 实现已迁入 runtime 侧
            # 不再从 ingest 离线管线 import（runtime→ingest 跨层边清零）。
            from runtime.knowledge_validation import validate
            from runtime.knowledge_loader import KnowledgePackage
            from pathlib import Path

            # GUI-1：knowledge_dir 绝对化（进程 cwd 可能被 Local 探测线程
            # chdir 污染——相对路径会解析到 M7 导致 validate 报缺文件）
            knowledge_dir = self._absolute_knowledge_dir(spec.knowledge_dir)
            pkg = KnowledgePackage(Path(knowledge_dir))
            errors, _ = validate(pkg, verbose=False)
            if errors:
                bus.publish(make_event("run_finished", execution_id,
                                       context={"result": "invalid", "errors": len(errors)}))
                self._set_state("idle")    # 清理状态，下次点击不受污染
                self._clear_runner()
                return "invalid"

            # 自动置顶/拉起：gate 检查前先激活游戏窗口（否则 foreground=False
            # 永远拦——激活发生在 orchestrator.run_mission 太晚）。
            # 窗口不存在 → 自动启动游戏（借鉴 m7 LocalGameController：
            # cmd start → 等窗口 360s → 点"点击进入"）。
            try:
                from runtime.drivers.local.window import find_game_window
                from runtime.win_capture import set_foreground_with_retry
                from runtime.platform.windows.game_launcher import ensure_game_launched
                game = find_game_window()
                if game is None:
                    bus.publish(make_event("state_changed", execution_id,
                                           detail="launching_game",
                                           context={"action": "launch_game"}))
                    ok, reason = ensure_game_launched()
                    if not ok:
                        bus.publish(make_event("state_changed", execution_id,
                                               detail=f"launch_failed:{reason}",
                                               context={"action": "launch_failed",
                                                        "reason": reason}))
                    game = find_game_window()
                if game:
                    set_foreground_with_retry(game["hwnd"])
                    # SetForegroundWindow 异步——前台切换
                    # 有延迟，立即 gate 检查 foreground 会误判 False 拦截。
                    # 等前台稳定（0.5s）再检查。
                    import time as _time
                    _time.sleep(0.5)
            except Exception as e:
                import logging
                logging.getLogger("runtime.api").warning(
                    "游戏启动/前台切换失败: %s: %s", type(e).__name__, e)
            gate = None
            targets = spec.target_ids or [c["id"] for c in pkg.chests]
            # ×24 防御：空目标 → 明确 no_targets（不能空跑后误报 all_done
            # all(空dict) 恒 True 是误操作源头）
            if not targets:
                bus.publish(make_event("run_finished", execution_id,
                                       context={"result": "no_targets"}))
                self._set_state("idle")
                self._clear_runner()
                return "no_targets"
            # 纯轨迹回放（自定义地图目标）：跳过 G3 环境门槛
            # 纯播放不需要 OCR/视觉/环境检测，秒开（0.6.0 修复）
            replay_only = True
            try:
                from pathlib import Path as _P
                # commands.py 在 runtime/api/（depth 2）→ parent³=repo root。
                # 注意：与 orchestrator/step_executor（depth 1, parent²）不同。
                _traj_dir = data_path("knowledge") / "trajectories"
                for _tid in targets:
                    _wf = pkg.workflow(_tid)
                    if _wf is not None:
                        _steps = _wf.get("steps") or []
                        if not _steps or not all(
                                _s.get("type") == "trajectory"
                                for _s in _steps):
                            replay_only = False
                            break
                    elif not (_traj_dir / f"{_tid}.json").exists():
                        replay_only = False
                        break
            except Exception:
                replay_only = False
            if not replay_only:
                gate = self._gate_check(bus, execution_id, pkg)
                if gate is not None:
                    self._clear_runner()
                    return gate
            else:
                bus.publish(make_event("state_changed", execution_id,
                                       detail="replay_only:skip_gate",
                                       context={"action": "replay_only"}))
            self._set_state("running")
            bus.publish(make_event("run_started", execution_id,
                                   context={"knowledge": knowledge_dir,
                                            "targets": targets, "mode": "real",
                                            "input_mode": "real",    # observe_only 可见
                                            "knowledge_hash": pkg.package_hash()}))
            try:
                if self._stop_event.is_set():
                    return "stopped"
                from runtime.orchestrator import WorkflowOrchestrator
                orch = WorkflowOrchestrator(pkg, bus=bus,
                                            execution_id=execution_id,
                                            stop_check=self._stop_event.is_set)
                # orchestrator 创建后、执行前
                # 再检查——F10 在启动期按下 → 不开始任何执行
                if self._stop_event.is_set():
                    self._set_state("stopped")
                    bus.publish(make_event("run_finished", execution_id,
                                           context={"result": "stopped"}))
                    self._clear_runner()
                    return "stopped"
                # F10-011：暂停等待——runner 在 run_mission 前阻塞，
                # 直到 resume 清除 _pause_event 或 stop 触发。
                # 限制：此暂停仅在 run_mission 前生效；执行中暂停需
                # orchestrator 逐步检查（当前不支持，用 stop 替代）。
                while self._pause_event.is_set():
                    if self._stop_event.is_set():
                        self._set_state("stopped")
                        bus.publish(make_event("run_finished", execution_id,
                                               context={"result": "stopped"}))
                        self._clear_runner()
                        return "stopped"
                    time.sleep(0.1)
                results, completed = orch.run_mission(targets)
                result = ("stopped" if self._stop_event.is_set()
                          else ("all_done" if all(results.values())
                                else "some_failed"))
                bus.publish(make_event("mission_summary", execution_id,
                                       context={"results": results,
                                                "completed_targets": completed,
                                                "records": orch.session_summary()}))
                # 审查：stopped 必须保留 stopped 状态（原无条件设 done
                # 停止后 runtime state 被覆盖成 done，语义错误）
                self._set_state("stopped" if result == "stopped" else "done")
                bus.publish(make_event("run_finished", execution_id,
                                       context={"result": result}))
                self._clear_runner()
                return result
            except Exception as e:    # real 执行异常 → 显式 run_finished(crashed)，GUI 不永久卡运行
                import traceback
                traceback.print_exc()
                # 异常由外部停止引起（界面归一化
                # 期 F10）→ 归 stopped 而非 crashed（急停≠崩溃，UI 不该报故障）
                if self._stop_event.is_set():
                    self._set_state("stopped")
                    bus.publish(make_event("run_finished", execution_id,
                                           context={"result": "stopped",
                                                    "error": str(e)}))
                    self._clear_runner()
                    return "stopped"
                self._set_state("crashed")
                bus.publish(make_event("run_finished", execution_id,
                                       context={"result": "crashed",
                                                "error": str(e)}))
                self._clear_runner()
                return "crashed"

        import uuid

        self.execution_id = self.execution_id or f"run{uuid.uuid4().hex[:4]}"
        # GUI-2：runner_factory 注入（诊断/测试用）——此前参数存在但被忽略
        make_runner = runner_factory or (lambda bus_, eid: runner(bus_, eid))
        self._thread = threading.Thread(
            target=lambda: make_runner(self.bus, self.execution_id), daemon=True,
            name="RuntimeAPI-runner")    # 线程命名（日志可辨识）
        self._thread.start()
        return self.execution_id

    @staticmethod
    def _absolute_knowledge_dir(knowledge_dir):
        """相对知识目录 → 基于项目根目录绝对化（不受进程 cwd 影响）。"""
        from pathlib import Path
        p = Path(knowledge_dir)
        if p.is_absolute():
            return str(p)
        # commands.py 在 runtime/api/（depth 2）→ parent³=repo root。
        root = Path(__file__).resolve().parent.parent.parent
        return str(root / knowledge_dir)

    def _gate_check(self, bus, execution_id, pkg):
        """G3 门槛：健康检查（企划 v0.12.2 §2.4）。

        只硬拦"缺了真没法跑"的能力：
          critical = window/capture/ocr（没窗口/截不了图/认不了字）
          warning = foreground/admin/input L0-L2（提示风险，不拦截：
            非管理员对同权限游戏 SendInput 有效（UIPI 只拦低→高）；
            前台拉置顶失败执行中会自然重试——硬拦导致"点开始就环境
            不对"的挫败感，且把可尝试的任务全挡）

        spec.requires 可追加任务必需能力键（仍硬拦）。
        """
        from runtime.health import check_health
        from runtime.capability import detect_capability
        # input_probe/auto_activate：真机 gate 才做 L2 按键注入 + 激活游戏前台
        #（会按 ESC 并抢前台——GUI 启动的健康检查不能打扰用户）
        h = check_health(input_probe=True, auto_activate=True)
        cap = h["capability"]
        critical = ["window", "capture", "ocr"]
        fails = [k for k in critical if not cap.get(k)]
        # 非硬性项：提示但不拦截（前台/管理员/输入注入/视觉模型 key）
        warns = [k for k in ("foreground", "admin", "input_l0", "input_l1")
                 if cap.get(k) is False]
        if cap.get("input_l2") is False:
            warns.append("input_l2")
        for req in (self._pending_requires or []):
            if req not in cap or not cap.get(req):
                fails.append(req)
        if fails:
            # 目标 4：能力报告——区分 OBSERVE_ONLY（可观测但输入被拦）与 BLOCKED（观测不可用）
            # 审查 P1：detect_capability 期望扁平键（window/capture/...）
            # 传嵌套 dict 会全取 False。解包 capability 层
            cap_report = detect_capability(h.get("capability") or {})
            bus.publish(make_event("run_finished", execution_id,
                                   context={"result": "gate_blocked",
                                            "fails": fails,
                                            "mode": cap_report.mode,
                                            "reasons": cap_report.reasons,
                                            "errors": h["errors"]}))
            self._set_state("gate_blocked")
            return "gate_blocked"
        if warns:
            # 非硬性警告：随 run_started 发出（GUI 提示，不拦截）
            bus.publish(make_event("state_changed", execution_id,
                                   detail="gate_warnings",
                                   context={"warns": warns,
                                            "errors": h["errors"]}))
        return None

    def pause(self):
        """F10-011 修复：真暂停——设置 _pause_event，runner 在 run_mission 前阻塞。

        保证：调用 pause 后，runner 线程在 run_mission 前进入等待，
        直到 resume 清除 _pause_event 或 stop 触发。
        限制：此暂停仅在 run_mission 前生效；执行中暂停需 orchestrator
        逐步检查（当前不支持，用 stop 替代）。
        """
        self._pause_event.set()
        self._set_state("paused")
        self.bus.publish(make_event("pause_requested", self.execution_id,
                                    context={"reason": "user_pause"}))
        return self._state

    def request_pause_human(self, reason="unknown_state"):
        self._set_state("paused_for_human")
        self.bus.publish(make_event("pause_requested", self.execution_id,
                                    context={"reason": reason}))
        return self._state

    def resume_check(self, checks=None):
        self._set_state("resume_check")
        self.bus.publish(make_event("resume_checked", self.execution_id,
                                    context={"checks": checks or []}))
        return self._state

    def resume(self):
        """F10-011 修复：真恢复——清除 _pause_event，runner 继续执行。

        保证：调用 resume 后，阻塞在 pause 等待中的 runner 线程
        立即解除阻塞，继续执行 run_mission。
        """
        self._pause_event.clear()
        self._set_state("running")
        self.bus.publish(make_event("resume_checked", self.execution_id,
                                    context={"checks": []}))
        return self._state

    @property
    def state(self):
        """公开状态（ GUI 不直接读私有 _state）。"""
        return self._state

    def _clear_runner(self):
        """runner 线程完成 → 清引用（防重入检查依赖 _thread.is_alive）。"""
        self._runner = None
        self._thread = None

    def stop(self):
        # 真停止——置信号；orchestrator 每 step 检查 stop_check 即中断
        self._stop_event.set()
        # F10-011：清除暂停（stop 优先于 pause——runner 不应阻塞在暂停等待中）
        self._pause_event.clear()
        # 停止即清理运行上下文（防下次启动残留旧任务状态）。
        # 审查：保留 _thread 参考（不置 None）——start_mission 防重入靠
        # is_alive 判断旧线程是否真退出（置 None 后无法判断 → 双执行风险）
        self._runner = None
        self._set_state("stopped")
        return self._state

    def inspect(self):
        return {"state": self._state, "execution_id": self.execution_id}

    def recent_events(self, limit=50):
        return self.bus.replay(self.execution_id)[-limit:]
