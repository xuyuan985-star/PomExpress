"""ErrorCode： -A 错误语义稳定化——错误判定优先走枚举，字符串仅展示。

背景：error 字段是字符串（如 "no_observation:chest_A"），retryable/subclass
判定若持续做子串匹配，未来 error 变体增多必然腐化。本模块提供：
- ErrorCode：当前全部错误信号收敛为枚举值（稳定契约）
- code_of：error 字符串 → ErrorCode（后缀 `:target` 容忍）
- 判定表：code → 子分类 / retryable / 恢复建议（单一来源）

字符串子串表（FAILURE_SUBCLASSES / PERMANENT_MARKERS）保留为 backend
外来错误文本的兜底；本模块的 code 优先。
"""
from enum import Enum


class DomainError(Exception):
    """领域异常基类——按模块分类，携带 error_code。"""

    def __init__(self, message, error_code=None):
        super().__init__(message)
        self.error_code = error_code

    def to_dict(self):
        return {"type": type(self).__name__, "message": str(self),
                "error_code": self.error_code}


class VisionError(DomainError):
    """视觉链路异常（截图/匹配/OCR）。"""


class InputError(DomainError):
    """输入链路异常（鼠标/键盘/窗口/权限）。"""


class ConfigError(DomainError):
    """配置异常（缺失/非法值/版本不匹配）。"""


class StateError(DomainError):
    """状态机/状态异常（非法迁移/损坏状态）。"""


class ErrorCode(str, Enum):
    OBS_MISSING = "no_observation"              # 目标从未被观测到
    OBS_STALE = "stale_observation"             # 观测过期（>TTL）
    OBS_LOW_CONFIDENCE = "low_confidence"       # 观测置信度低于阈值
    INVALID_BBOX = "invalid_bbox_format"        # 观测 bbox 格式非法
    TEMPLATE_MISSING = "unknown_entity"         # 实体无模板/模板缺失
    METHOD_UNKNOWN = "unknown_method"           # method 未注册
    CLICK_TEMPLATE_FAILED = "click_element_failed"    # 模板点击失败（transient）
    CLICK_TEXT_FAILED = "click_text_failed"           # 文字点击失败（transient）
    EXECUTOR_EXCEPTION = "executor_exception"         # 执行器内部异常（可观测失败）
    PERMISSION_BLOCKED = "uipi_block"           # UIPI/权限拦截
    GATE_BLOCKED = "gate_blocked"               # G3 能力门槛未过
    VERIFY_TIMEOUT = "verify_timeout"           # verify 等待超时（结果不确定）
    MOVE_ABORTED = "move_aborted"               # 移动被 emergency/stall 中断
    MOVE_STUCK = "move_stuck"                   # 移动卡死（目标不动）
    PRECONDITION_BLOCKED = "precondition"       # 动作前置条件不满足
    VISION_UNTRUSTED = "vision_untrusted"       # Sprint B：画面内容不可信（多信号不足）
    ACTION_BLOCKED = "action_blocked"           # Sprint C：策略层拒绝（风险过高）
    UNKNOWN = "unknown_error"                   # 未归类（兜底）


# code → 失败子分类（F1/F2/F3 主类冻结，后缀供训练/分析；F4_VISION 为
# Sprint 新增主类——视觉可信度失败与 F3 世界状态失败分离）
SUBCLASS_BY_CODE = {
    ErrorCode.OBS_MISSING: "F2_COORD",
    ErrorCode.OBS_STALE: "F2_COORD",
    ErrorCode.OBS_LOW_CONFIDENCE: "F2_COORD",
    ErrorCode.INVALID_BBOX: "F2_COORD",
    ErrorCode.TEMPLATE_MISSING: "F1_TEMPLATE",
    ErrorCode.METHOD_UNKNOWN: "F1_INTERNAL",
    ErrorCode.CLICK_TEMPLATE_FAILED: "F1_TEMPLATE",
    ErrorCode.CLICK_TEXT_FAILED: "F1_TEMPLATE",
    ErrorCode.EXECUTOR_EXCEPTION: "F1_EXEC",
    ErrorCode.PERMISSION_BLOCKED: "F6_PRIVILEGE",     # Sprint D-10：权限/提权失败独立主类
    ErrorCode.GATE_BLOCKED: "F6_PRIVILEGE",
    ErrorCode.VERIFY_TIMEOUT: "F2_TIMEOUT",
    ErrorCode.MOVE_ABORTED: "F2_COORD",
    ErrorCode.MOVE_STUCK: "F2_COORD",
    ErrorCode.PRECONDITION_BLOCKED: "F3",
    ErrorCode.VISION_UNTRUSTED: "F4_VISION",
    ErrorCode.ACTION_BLOCKED: "F5_ACTION_BLOCK",
}

# Sprint C：F5 子分类特征
ACTION_SUBCLASSES = [
    ("ACTION_RISK_HIGH", "F5_RISK_HIGH"),
    ("risk", "F5_RISK_HIGH"),
]

# Sprint F4_VISION 子分类特征（error/reason 文本匹配）
VISION_SUBCLASSES = [
    ("dark", "F4_DARK"),               # 黑屏
    ("wrong_window", "F4_WRONG_WINDOW"),
    ("size_mismatch", "F4_WRONG_WINDOW"),
    ("conflict", "F4_CONFLICT"),       # 观察通道冲突
    ("不一致", "F4_CONFLICT"),
    ("low confidence", "F4_LOW_CONF"),
    ("VISION_NOT_VERIFIED", "F4_NOT_VERIFIED"),
    ("VISION_LOW_CONFIDENCE", "F4_LOW_CONF"),
    ("VISION_EXPIRED", "F4_EXPIRED"),
    ("frame", "F4_FRAME"),             # 帧结构异常
]

# code → retryable（permanent 失败不重试；transient 可重试）
PERMANENT_CODES = {
    ErrorCode.OBS_MISSING, ErrorCode.OBS_STALE, ErrorCode.OBS_LOW_CONFIDENCE,
    ErrorCode.INVALID_BBOX, ErrorCode.TEMPLATE_MISSING, ErrorCode.METHOD_UNKNOWN,
    ErrorCode.EXECUTOR_EXCEPTION, ErrorCode.PERMISSION_BLOCKED,
    ErrorCode.GATE_BLOCKED, ErrorCode.VISION_UNTRUSTED,
}

# code → 恢复建议（reobserve / alternative / retry / abort）
RECOVERY_BY_CODE = {
    ErrorCode.OBS_MISSING: "reobserve",
    ErrorCode.OBS_STALE: "reobserve",
    ErrorCode.OBS_LOW_CONFIDENCE: "reobserve",
    ErrorCode.CLICK_TEMPLATE_FAILED: "alternative",
    ErrorCode.CLICK_TEXT_FAILED: "alternative",
    ErrorCode.TEMPLATE_MISSING: "abort",
    ErrorCode.METHOD_UNKNOWN: "abort",
    ErrorCode.PERMISSION_BLOCKED: "abort",
    ErrorCode.GATE_BLOCKED: "abort",
}


def code_of(error):
    """error 字符串 → ErrorCode（容忍 ":target" 后缀；未归类 → UNKNOWN）。"""
    if not error:
        return None
    for code in ErrorCode:
        if error == code.value or error.startswith(code.value + ":"):
            return code
    return ErrorCode.UNKNOWN
