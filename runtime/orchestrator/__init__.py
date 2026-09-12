"""runtime.orchestrator — 编排器包。

拆分自原 runtime/orchestrator.py 单体：
- SessionWatchdog → watchdog.py
- TargetRecord → records.py
- WorkflowOrchestrator → _orchestrator_core.py

向后兼容：from runtime.orchestrator import X 仍然可用。
"""
from runtime.orchestrator.watchdog import SessionWatchdog
from runtime.orchestrator.records import TargetRecord

# WorkflowOrchestrator 从 _orchestrator_core.py 导入（原 orchestrator.py 已重命名）。
# 注意：此处禁止写 except 回退 `from runtime.orchestrator import ...`——那是包
# 自身的自杀式递归导入（ImportError 时无限重进 __init__）。导入失败就直接抛，
# 让调用方第一时间看见，而不是递归爆栈。
from runtime._orchestrator_core import (
    WorkflowOrchestrator,
    STEP_TYPES,
    MAX_TARGET_ATTEMPTS,
    WATCHDOG_STALL_SECONDS,
    ROOT,
    ALREADY_OPENED_TEMPLATE_THRESHOLD,
    ALREADY_OPENED_MINIMAP_THRESHOLD,
    ALREADY_OPENED_FALLBACK_OCR,
)

__all__ = [
    "WorkflowOrchestrator",
    "SessionWatchdog",
    "TargetRecord",
    "STEP_TYPES",
    "MAX_TARGET_ATTEMPTS",
    "WATCHDOG_STALL_SECONDS",
    "ROOT",
    "ALREADY_OPENED_TEMPLATE_THRESHOLD",
    "ALREADY_OPENED_MINIMAP_THRESHOLD",
    "ALREADY_OPENED_FALLBACK_OCR",
]
