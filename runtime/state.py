"""P1-008：统一任务状态（MissionState）——消除多套状态体系漂移。

此前：RuntimeAPI（idle/running/done/crashed/stopped/gate_blocked/paused...）
与 CommandDeck（pending/running/succeeded/failed/interrupted）各说各话。
统一枚举后：Runtime 层输出枚举值，UI 层只做展示映射。
"""
from enum import Enum


class MissionState(Enum):
    IDLE = "idle"
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCESS = "success"
    FAILED = "failed"
    CRASHED = "crashed"
    STOPPED = "stopped"
    GATE_BLOCKED = "gate_blocked"
    INVALID = "invalid"


# 状态字符串 → 枚举（RuntimeAPI 与事件里都以字符串传递状态）
_STATE_MAP = {
    "idle": MissionState.IDLE,
    "running": MissionState.RUNNING,
    "done": MissionState.SUCCESS,
    "failed": MissionState.FAILED,
    "crashed": MissionState.CRASHED,
    "stopped": MissionState.STOPPED,
    "gate_blocked": MissionState.GATE_BLOCKED,
    "invalid": MissionState.INVALID,
    "paused": MissionState.PAUSED,
    "paused_for_human": MissionState.PAUSED,
    "resume_check": MissionState.RUNNING,
}


def normalize_state(state):
    """任意来源状态 → MissionState（字符串/枚举兼容）。"""
    if isinstance(state, MissionState):
        return state
    return _STATE_MAP.get(str(state), MissionState.IDLE)
