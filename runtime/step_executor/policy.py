"""失败分类/恢复策略常量与辅助函数。

从原 runtime/step_executor.py 单体中拆出的策略层。
"""
from runtime.errors import code_of


# 只对这些动作执行自然性 sleep：查询/verify/noop 不应人为等待
INPUT_ACTIONS = {"interact", "click_text", "move", "click"}

# 失败子分类：保持 F1/F2/F3 主类（v0.12.1 冻结），后缀细化供训练/分析
FAILURE_SUBCLASSES = [
    ("uipi", "F6_PRIVILEGE"),
    ("admin", "F6_PRIVILEGE"),
    ("observe_only", "F6_PRIVILEGE"),
    ("executor_exception", "F1_EXEC"),
    ("unknown_method", "F1_INTERNAL"),
    ("unknown_entity", "F1_TEMPLATE"),
    ("click_element_failed", "F1_TEMPLATE"),
    ("click_text_failed", "F1_TEMPLATE"),
    ("no_observation", "F2_COORD"),
    ("invalid_bbox_format", "F2_COORD"),
    ("low_confidence", "F2_COORD"),
    ("stale_observation", "F2_COORD"),
    ("verify_timeout", "F2_TIMEOUT"),
]

# permanent 失败（ 的 retryable=False）——重试无意义，直接失败
PERMANENT_MARKERS = (
    "unknown_entity", "unknown_method", "invalid_bbox_format",
    "no_observation", "low_confidence", "stale_observation",
    "executor_exception", "uipi_block", "gate_blocked",
    "observe_only",
)

# RecoveryPolicy：失败 → 恢复建议（轻量版，供 orchestrator/GUI 决策）
RECOVERY_POLICY = [
    ("no_observation", "reobserve"),
    ("stale_observation", "reobserve"),
    ("low_confidence", "reobserve"),
    ("click_element_failed", "alternative"),
    ("click_text_failed", "alternative"),
    ("unknown_entity", "abort"),
    ("unknown_method", "abort"),
    ("uipi_block", "abort"),
    ("gate_blocked", "abort"),
]


def recovery_for(error):
    """按失败特征给出恢复策略建议。"""
    if not error:
        return "retry"
    from runtime.errors import RECOVERY_BY_CODE
    code = code_of(error)
    if code is not None and code in RECOVERY_BY_CODE:
        return RECOVERY_BY_CODE[code]
    for key, action in RECOVERY_POLICY:
        if key in error:
            return action
    return "retry"


# 观测时效
OBS_MAX_AGE = 1.5
# 观测置信度下限
OBS_MIN_CONFIDENCE = 0.6

# 可调 · 方法签名默认阈值（step_executor._step_executor_core.py 使用）
# 语义边界（勿合并）：
# - interact_template: 主动与实体交互——最严，0.60（避免误触相邻 UI）
# - portal_transition / verify_signal: 常规 0.8
# - _click_template_hit: map 轨迹点击——最严 0.9（map 轨迹已录好，宁可跳过不可误点）
# - step.get("threshold", ...): workflow step 里的兜底值——同 _click_template_hit
# 取值范围 (0, 1]，超出请改这里。
THRESHOLD_INTERACT_TEMPLATE = 0.60
THRESHOLD_PORTAL = 0.8
THRESHOLD_VERIFY_SIGNAL = 0.8
THRESHOLD_CLICK_TEMPLATE_HIT = 0.9
# workflow step 里未写 threshold 时的兜底值（map 轨迹点击路径）
STEP_THRESHOLD_DEFAULT = 0.9


def subclass_for(error):
    if not error:
        return None
    for key, sub in FAILURE_SUBCLASSES:
        if key in error:
            return sub
    return None


def retryable_for(error):
    """恢复策略：permanent 失败不重试，transient 重试。"""
    if not error:
        return True
    return not any(m in error for m in PERMANENT_MARKERS)
