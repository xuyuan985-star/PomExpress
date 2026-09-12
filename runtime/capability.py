"""CapabilityReport（目标 4：能力检测——失败恢复体系的前置）。

ISSUE-09（SendInput ret=0 / UIPI）不是代码 bug，是 Windows 权限现实。
对策 = 启动时检测能力，而不是运行中点击失败才暴露：

    READY → 全通道可用，可进入 EXECUTE
    OBSERVE_ONLY → 捕获/OCR 可用但输入被拦（UIPI/非管理员）→ 只观测不执行
    BLOCKED → 捕获不可用（窗口没开等）→ 不进入执行

detect_capability 复用 health.check_health 的通道探测（平铺 dict）。
"""
from dataclasses import dataclass, field


@dataclass
class CapabilityReport:
    window: bool = False
    capture: bool = False
    ocr: bool = False
    input_l0: bool = False
    input_l1: bool = False
    input_l2: bool = False
    admin: bool = False
    foreground: bool = False
    mode: str = "BLOCKED"         # READY | OBSERVE_ONLY | BLOCKED
    reasons: list = field(default_factory=list)

    @property
    def input_available(self):
        return all((self.input_l0, self.input_l1, self.input_l2,
                    self.admin, self.foreground))

    @property
    def capture_available(self):
        return self.window and self.capture

    def to_context(self):
        return {
            "mode": self.mode,
            "window": self.window, "capture": self.capture,
            "ocr": self.ocr,
            "input": self.input_available, "admin": self.admin,
            "foreground": self.foreground,
            "reasons": self.reasons,
        }


def detect_capability(health_dict=None):
    """从 health.check_health 结果汇总能力报告。

    health_dict 为 None 时调用 health.check_health（真机探测；
    无驱动环境会失败 → BLOCKED + reason）。
    """
    if health_dict is None:
        try:
            from runtime.health import check_health
            health_dict = check_health() or {}
        except Exception:
            # 异常带完整堆栈（health 探测失败原因可定位）
            import logging
            logging.getLogger("runtime.capability").exception(
                "health probe failed")
            return CapabilityReport(mode="BLOCKED",
                                    reasons=["health probe failed"])
    rep = CapabilityReport(
        window=bool(health_dict.get("window")),
        capture=bool(health_dict.get("capture")),
        ocr=bool(health_dict.get("ocr")),
        input_l0=bool(health_dict.get("input_l0")),
        input_l1=bool(health_dict.get("input_l1")),
        input_l2=bool(health_dict.get("input_l2")),
        admin=bool(health_dict.get("admin")),
        foreground=bool(health_dict.get("foreground")),
    )
    if not rep.capture_available:
        rep.mode = "BLOCKED"
        rep.reasons.append("窗口/捕获不可用（游戏未启动或窗口未找到）")
    elif not rep.input_available:
        rep.mode = "OBSERVE_ONLY"
        rep.reasons.append("输入被拦（UIPI/非管理员/前台不满足）——只观测不执行")
    elif not rep.ocr:
        rep.mode = "OBSERVE_ONLY"
        rep.reasons.append("观测通道不可用（OCR 未就绪）")
    else:
        rep.mode = "READY"
    return rep
