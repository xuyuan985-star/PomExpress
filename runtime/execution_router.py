"""ExecutionRouter（第 9 步：执行分派——按可用性降级）。

Planner 产出 ActionIntent → router 选择可用 driver 执行：
    RealInputDriver(Local) → ReplayInput(测试) → ObserveOnlyInput(兜底)
任何情况下不崩溃：输入不可用 → observe_only 结构化失败
（ISSUE-09 SendInput=0 / ISSUE-11 UAC 卡死 → OBSERVE_ONLY 正常运行）。
"""
from runtime.input.base import InputBackendProtocol
from runtime.input.observe import ObserveOnlyInput


class ExecutionRouter:
    """按 available 优先级选择输入后端。

    .input → 当前可用后端（全部不可用 → ObserveOnlyInput 兜底）
    .execute → 直接执行（对单原语接口）
    from_capability → 按能力报告构造驱动栈
    """

    def __init__(self, drivers=None, fallback=None):
        self.drivers = list(drivers or [])
        self.fallback = fallback or ObserveOnlyInput()

    @property
    def input(self) -> InputBackendProtocol:
        for d in self.drivers:
            if getattr(d, "available", True):
                return d
        return self.fallback

    def execute(self, method, *args, **kwargs):
        backend = self.input
        fn = getattr(backend, method, None)
        if fn is None:
            from runtime.input.base import InputResult
            return InputResult(success=False, action=method, backend=backend.name,
                               error=f"unknown_method:{method}")
        try:
            # 路由层是系统边界——backend 异常必须转为 InputResult
            #（不能假设所有 backend 都自兜底）
            return fn(*args, **kwargs)
        except Exception as e:
            import logging
            logging.getLogger("runtime.router").exception(
                "Input backend %s.%s 执行异常", backend.name, method)
            from runtime.input.base import InputResult
            return InputResult(success=False, action=method, backend=backend.name,
                               error=f"{type(e).__name__}: {e}")
    @staticmethod
    def from_capability(cap_report, real=None):
        """按 CapabilityReport 构造驱动栈：

        READY → [real(可用), observe]
        OBSERVE_ONLY → [observe]（真实 driver 标记不可用）
        BLOCKED → [observe]
        """
        real_driver = real
        if real_driver is not None and cap_report is not None:
            real_driver.available = cap_report.input_available
        return ExecutionRouter(drivers=[d for d in [real_driver] if d is not None])
