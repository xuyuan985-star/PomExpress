"""统一应用生命周期状态（AppState）——初始化中/已运行/停止中。"""
from enum import Enum


class AppState(Enum):
    INITIALIZING = "initializing"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class StartupStage(Enum):
    """启动阶段——卡在哪一步可明确报告。"""
    CONFIG = "config"
    KNOWLEDGE = "knowledge"
    RUNTIME = "runtime"
    GUI = "gui"
    READY = "ready"
    FAILED = "failed"


class AppLifecycle:
    def __init__(self, on_change=None):
        self._state = AppState.INITIALIZING
        self._on_change = on_change

    @property
    def state(self):
        return self._state

    def set(self, state):
        if isinstance(state, str):
            state = AppState(state)
        if state != self._state:
            old = self._state
            self._state = state
            if self._on_change:
                try:
                    self._on_change(old, state)
                except Exception:
                    pass

    def is_active(self):
        return self._state in (AppState.RUNNING, AppState.INITIALIZING)


LIFECYCLE = AppLifecycle()
