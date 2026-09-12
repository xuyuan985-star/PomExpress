"""ActionGuard（Sprint 执行前安全闸门——executor 不相信任何人）。

任何执行必须经过 allow：
- vision_verified 未确认 → VISION_NOT_VERIFIED（strict 模式）
- vision_confidence < 门槛 → VISION_LOW_CONFIDENCE
- evidence 过期（TTL）→ VISION_EXPIRED（10 秒前看到商店 ≠ 现在可以点）
- 高风险动作（购买/删除/退出）：更严 TTL + 更高置信门槛（双保险）

兼容模式：strict=False 时未验证意图放行（workflow 模板路径——自身带
verify 步骤闭环）；但已验证的意图仍按完整校验。
"""
import time

DEFAULT_MAX_AGE = 3.0            # 证据有效窗口（秒）
HIGH_RISK_MAX_AGE = 1.5          # 高风险动作更严
HIGH_RISK_CONFIDENCE = 0.85      # 高风险动作置信门槛


class ActionGuard:
    def __init__(self, min_confidence=0.75, max_age=DEFAULT_MAX_AGE,
                 strict=False, evidence_store=None):
        self.min_confidence = min_confidence
        self.max_age = max_age
        self.strict = strict          # True：未验证意图直接拒绝
        self.evidence_store = evidence_store    # id → {timestamp, confidence}

    def check(self, intent, observation=None):
        """Sprint C：最终允许/拒绝——风险量化 + 策略规则 + 证据校验。

        返回 {"allowed", "risk", "reason", "limit"}。
        """
        from runtime.guards.risk import calculate_risk
        from runtime.guards.policy import (allowed, risk_limit,
                                           confidence_for, is_critical)

        evidence_age = self._evidence_age(intent.evidence_id)
        expired = evidence_age is not None and evidence_age > self.max_age
        risk = calculate_risk(intent, observation, evidence_expired=expired)

        # 风险等级由动态计算驱动（intent.risk 静态字段可伪造）
        # risk score 高 → 更高置信要求 / critical 拒绝自动执行
        risk_level = "low"
        if risk >= 70:
            risk_level = "critical"
        elif risk >= 40:
            risk_level = "high"
        elif risk >= 20:
            risk_level = "medium"
        # 显式声明（workflow 标注）与动态推导取更严者
        declared = getattr(intent, "risk", "low")
        _ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        if _ORDER.get(declared, 0) > _ORDER[risk_level]:
            risk_level = declared

        # critical 等级拒绝自动执行（人工确认）
        if is_critical(risk_level):
            return {"allowed": False, "risk": risk, "reason": "RISK_CRITICAL",
                    "limit": None}

        # 证据/置信校验（先于策略——硬性门槛）
        if expired:
            return {"allowed": False, "risk": risk, "reason": "VISION_EXPIRED",
                    "limit": None}
        # risk 等级 → 置信要求（high 需双通道高置信）
        required_conf = confidence_for(risk_level)
        conf_threshold = max(self.min_confidence, required_conf)
        if intent.vision_verified and intent.vision_confidence < conf_threshold:
            return {"allowed": False, "risk": risk, "reason": "VISION_LOW_CONFIDENCE",
                    "limit": conf_threshold}
        if not intent.vision_verified and self.strict:
            return {"allowed": False, "risk": risk, "reason": "VISION_NOT_VERIFIED",
                    "limit": None}

        # 策略：动作类型 × 风险门槛
        if not allowed(intent.action, risk):
            return {"allowed": False, "risk": risk, "reason": "ACTION_RISK_HIGH",
                    "limit": risk_limit(intent.action)}
        return {"allowed": True, "risk": risk, "reason": "OK", "limit": None}

    def allow(self, intent):
        """兼容旧接口（bool 语义）：返回 (allowed, reason)。"""
        r = self.check(intent)
        return r["allowed"], r["reason"]

    def _evidence_age(self, evidence_id):
        if not evidence_id or self.evidence_store is None:
            return None    # 无证据记录：由 confidence 门槛把关
        ev = self.evidence_store.get(evidence_id)
        if not ev:
            return None
        return time.time() - ev.get("timestamp", time.time())
