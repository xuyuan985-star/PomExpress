"""runtime.step_executor — 步骤执行器包。

拆分自原 runtime/step_executor.py 单体：
- 常量/策略函数 → policy.py
- RealExecutor → _step_executor_core.py

向后兼容：from runtime.step_executor import X 仍然可用。
"""
from runtime.step_executor.policy import (
    INPUT_ACTIONS,
    FAILURE_SUBCLASSES,
    PERMANENT_MARKERS,
    RECOVERY_POLICY,
    OBS_MAX_AGE,
    OBS_MIN_CONFIDENCE,
    recovery_for,
    subclass_for,
    retryable_for,
)

# RealExecutor 从 _step_executor_core.py 导入
from runtime import _step_executor_core as _step_executor_core    # noqa: F401 (mock.patch 目标暴露)
from runtime._step_executor_core import (
    RealExecutor,
    ROOT,
)

__all__ = [
    "RealExecutor",
    "INPUT_ACTIONS",
    "FAILURE_SUBCLASSES",
    "PERMANENT_MARKERS",
    "RECOVERY_POLICY",
    "OBS_MAX_AGE",
    "OBS_MIN_CONFIDENCE",
    "recovery_for",
    "subclass_for",
    "retryable_for",
    "ROOT",
]
