"""Risk Score（Sprint C-3：动作风险量化——为 Policy 提供决策输入）。

风险来源（可解释，简单累加）：
  +50 无观测 或 观测未被接受
  +30 危险动作（confirm / purchase / delete / exit / use_resource）
  +10 证据过期
"""
DANGEROUS_ACTIONS = {"confirm", "purchase", "delete", "exit", "use_resource"}


def calculate_risk(intent, observation=None, evidence_expired=False):
    """intent + observation → risk score（0~100+）。"""
    risk = 0

    # 无观测 → 未经任何视觉确认，风险最高档
    if observation is None:
        risk += 50
    elif not getattr(observation, "accepted", True):
        risk += 50

    # 危险动作
    if getattr(intent, "action", None) in DANGEROUS_ACTIONS:
        risk += 30

    # 证据过期
    if evidence_expired:
        risk += 10

    return risk
