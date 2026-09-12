"""ChestState（A 线 P0 黑塔 1 舱段）：chest 粒度完成状态持久化。

设计：
- mission_state:{pkg_key} 单 key 原子（与 MissionController 一致的持久化域）
- version=4：在原有 {version, completed, saved_at} 基础上挂 chest_done 表
  chest_done: { chest_id: {timestamp, rarity, verified_at, room, workflow} }
- 兼容 v3（只有 completed 列表）→ 自动迁移到 v4
- QSettings 写入（PySide6）；非 Qt 环境（pytest）用临时 JSON 文件兜底

API：
  load(pkg_key) → dict（含 completed 列表 + chest_done 表）
  mark_done(pkg_key, chest_id, **meta) → 单 chest 原子写
  mark_undone(pkg_key, chest_id) → 撤销（测试/人工）
  is_done(pkg_key, chest_id) → bool
  all_done(pkg_key, expected) → 全部完成判定
"""
import json
import logging
import os
import threading
import time
from pathlib import Path
from config.settings import data_path
from config.settings import QSETTINGS_APP, QSETTINGS_ORG

log = logging.getLogger("runtime.chest_state")

STATE_VERSION = 4

# 非 Qt 环境（pytest/CLI）兜底持久化目录。
# 可用环境变量 CS_FALLBACK_DIR 覆盖——测试应指向临时目录，避免把测试用的
# pkg_key 残留写进真实状态目录（state/ 是运行时数据，不是测试草稿区）。
_FALLBACK_DIR = Path(
    os.environ.get("CS_FALLBACK_DIR")
    or data_path("state")
)
_FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
_FALLBACK_LOCK = threading.RLock()

# Qt 环境标记（避免 pytest 引入 PySide6 依赖）
_QT_AVAILABLE = None


def _qt_settings():
    global _QT_AVAILABLE
    if _QT_AVAILABLE is None:
        # 测试/CLI 环境（无 QApplication 或显式 CS_FORCE_FALLBACK=1）→ 走 JSON 文件
        import os
        if os.environ.get("CS_FORCE_FALLBACK"):
            _QT_AVAILABLE = False
        else:
            try:
                from PySide6.QtCore import QSettings    # noqa: F401
                from PySide6.QtWidgets import QApplication
                app = QApplication.instance()
                _QT_AVAILABLE = app is not None
            except Exception:
                _QT_AVAILABLE = False
    return _QT_AVAILABLE


def _key(pkg_key: str) -> str:
    return f"mission_state:{pkg_key}"


def _fallback_path(pkg_key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in pkg_key)[:48]
    return _FALLBACK_DIR / f"mission_state_{safe}.json"


def _read_raw(pkg_key: str):
    if _qt_settings():
        from PySide6.QtCore import QSettings
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        return s.value(_key(pkg_key))
    p = _fallback_path(pkg_key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        # D5-B2：损坏/半写文件不得静默吞成空——记录警告（保留返回 None 行为
        # 以免上层崩溃，但用户/日志可见）
        log.warning("状态文件损坏，重置为空状态: %s (%s)", p, e)
        return None


def _write_raw(pkg_key: str, state):
    if _qt_settings():
        from PySide6.QtCore import QSettings
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        s.setValue(_key(pkg_key), state)
        s.sync()
        return
    # D5-B2：原子写（临时文件 + os.replace）——防止半写/崩溃导致文件损坏
    path = _fallback_path(pkg_key)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _FALLBACK_LOCK:
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8")
        os.replace(str(tmp), str(path))


def _empty_state() -> dict:
    return {
        "version": STATE_VERSION,
        "completed": [],
        "chest_done": {},
        "saved_at": None,
    }


def _migrate(raw) -> dict:
    """v3 → v4 迁移：把 completed 列表搬进 chest_done 表。"""
    if raw is None:
        return _empty_state()
    if not isinstance(raw, dict):
        return _empty_state()
    if raw.get("version") == STATE_VERSION:
        return raw
    # v3 → v4
    completed = raw.get("completed", []) or []
    chest_done = {}
    for cid in completed:
        if isinstance(cid, str):
            chest_done[cid] = {
                "timestamp": raw.get("saved_at"),
                "rarity": "unknown",
                "verified_at": None,
                "room": None,
                "workflow": None,
            }
    return {
        "version": STATE_VERSION,
        "completed": sorted(set(completed)),
        "chest_done": chest_done,
        "saved_at": raw.get("saved_at"),
    }


def load(pkg_key: str) -> dict:
    """读取完成状态（自动迁移到 v4）。"""
    return _migrate(_read_raw(pkg_key))


def mark_done(pkg_key: str, chest_id: str, **meta) -> dict:
    """单 chest 原子标记完成。

    meta 支持：rarity, room, workflow, verified_at, note
    同时更新 completed 列表（向后兼容 v3 消费方）与 chest_done 表。

    D5-B1：读改写序列在同一把锁内完成——消除 GUI 双写 + orchestrator
    收尾落盘并发时的 lost update。
    """
    with _FALLBACK_LOCK:
        state = load(pkg_key)
        now = time.time()
        entry = {
            "timestamp": now,
            "rarity": meta.get("rarity", "unknown"),
            "room": meta.get("room"),
            "workflow": meta.get("workflow"),
            "verified_at": meta.get("verified_at", now),
        }
        if meta.get("note"):
            entry["note"] = meta["note"]
        state["chest_done"][chest_id] = entry
        # completed 列表（v3 兼容）
        done = set(state.get("completed", []))
        done.add(chest_id)
        state["completed"] = sorted(done)
        state["saved_at"] = now
        _write_raw(pkg_key, state)
        return state


def mark_undone(pkg_key: str, chest_id: str) -> dict:
    """撤销完成（测试/人工）。

    D5-B1：读改写序列在同一把锁内完成。
    """
    with _FALLBACK_LOCK:
        state = load(pkg_key)
        state["chest_done"].pop(chest_id, None)
        completed = set(state.get("completed", []))
        completed.discard(chest_id)
        state["completed"] = sorted(completed)
        state["saved_at"] = time.time()
        _write_raw(pkg_key, state)
        return state


def is_done(pkg_key: str, chest_id: str) -> bool:
    """单 chest 是否已完成。"""
    state = load(pkg_key)
    if chest_id in state.get("chest_done", {}):
        return True
    # v3 兼容：completed 列表也查
    return chest_id in state.get("completed", [])


def pending_targets(pkg_key: str, expected) -> list:
    """返回未完成的目标列表（expected 为全目标集合）。"""
    state = load(pkg_key)
    done = set(state.get("chest_done", {}).keys()) | set(
        state.get("completed", []))
    return [t for t in expected if t not in done]


def all_done(pkg_key: str, expected) -> bool:
    """全部完成判定（空期望不视为完成——CLAUDE.md 踩坑清单）。"""
    if not expected:
        return False
    return all(is_done(pkg_key, t) for t in expected)


def clear(pkg_key: str) -> None:
    """清空（测试辅助）。"""
    _write_raw(pkg_key, _empty_state())
