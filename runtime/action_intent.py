"""Agent 内部动作协议（第 7 步：Planner 输出、Executor 消费）。

禁止包含：屏幕坐标 / DPI / 鼠标实现 / Windows API——只描述意图。
决策层（planner/orchestrator）零坐标（v0.12.1 Errata）。
"""
import uuid
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class ActionType(Enum):
    """动作语义（做什么）——与 ActionMethod（怎么做）正交。

    INTERACT：与实体交互（template / text 定位）
    PRESS_KEY：按键（含移动键 w/a/s/d）
    MOVE：移动到目标附近（视觉引导）
    VERIFY：验证信号
    WAIT / NONE：等待 / 无操作（决策层占位）
    """
    INTERACT = "interact"
    PRESS_KEY = "press_key"
    MOVE = "move"
    VERIFY = "verify"
    WAIT = "wait"
    NONE = "none"


def _deep_freeze(value):
    """-B：递归不可变化——MappingProxyType 只冻结表层 dict，
    嵌套 dict/list/set 仍可被改写（planner 偷偷改 intent 的隐患）。"""
    if isinstance(value, Mapping):
        return MappingProxyType({k: _deep_freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(v) for v in value)
    if isinstance(value, set):
        return frozenset(_deep_freeze(v) for v in value)
    return value


class ActionMethod(Enum):
    """intent.method 白名单：字符串散落是 typo 源头，运行时才炸。"""
    TEMPLATE = "template"
    TEXT = "text"
    KEY = "key"


class ActionState(Enum):
    """动作生命周期——统一状态（审计/UI/恢复可追踪）。"""
    CREATED = "created"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


# 非幂等动作——重复执行产生不同结果（禁 retry）
_NON_IDEMPOTENT_ACTIONS = {"confirm", "purchase", "delete", "exit",
                           "use_resource", "submit"}


@dataclass(frozen=True)
class ActionIntent:
    """动作意图：决策层产出，不携带任何坐标。

    不可变契约：
    - frozen + params=MappingProxyType——执行期间任何层（callback/retry/planner）
      都无法原地改写意图，防止"第一次 threshold=0.85，retry 后变 0.95"的隐式漂移。
    - id：意图唯一键（ b），action_started/action_completed 事件按 id 关联。

    语义约束（v0.12.1 Errata）：
    - target 一律为**世界实体 id**（chest_001 / lm_hall_center）或 UI 文字（method=text）；
      禁止模板文件名（chest_icon_v3.png 属于知识数据，由 executor 解析）。
    - 可经由 observation store 取实体最近观测位置，
      由 executor 解析绝对坐标——executor 知道"怎么点"，intent 决定"点什么"。
    """
    action: str            # interact | click_text | move | press_key
    target: str            # 世界实体 id / UI 文字
    method: str            # ActionMethod 值（构造时校验）
    params: Mapping = field(default_factory=dict)
    reason: str = None     # 决策意图（objective_verify_chest …）
    source: str = "decision_layer"
    idempotent: bool = True    # 非幂等动作（传送/确认/购买）禁止 retry
    execution_id: str = None    # 贯穿日志/失败关联（构造时由执行链传入）
    id: str = field(default_factory=lambda: f"int_{uuid.uuid4().hex[:8]}")
    preconditions: tuple = ()    # S5：执行前世界事实断言（契约先行，校验器接入前恒过）
    # evidence_id 关联证据生命周期（执行前 ActionGuard 据此判断证据是否过期）
    evidence_id: str = None
    risk: str = "low"            # low | high（购买/删除/退出——需更严确认）

    def __post_init__(self):
        if self.method not in ActionMethod._value2member_map_:
            raise ValueError(f"非法 method: {self.method}（允许: {sorted(m.value for m in ActionMethod)}）")
        # schema 校验——target 非空 / 置信度范围 / risk 白名单
        if not self.target and self.action != ActionType.WAIT.value:
            raise ValueError(f"动作 {self.action} 缺少 target")
        # risk 四级（policy.py）：low/medium/high/critical——critical 需人工确认
        if self.risk not in ("low", "medium", "high", "critical"):
            raise ValueError(f"非法 risk: {self.risk!r}（允许 low/medium/high/critical）")
        # 非幂等动作强制禁 retry（开发者忘设 idempotent=False 也不放行）
        if self.action in _NON_IDEMPOTENT_ACTIONS:
            object.__setattr__(self, "idempotent", False)
        object.__setattr__(self, "params", _deep_freeze(self.params))

    def to_context(self):
        method = self.method.value if isinstance(self.method, ActionMethod) \
            else self.method    # 枚举归一化为字符串（防 JSON 序列化崩）
        ctx = {"action": self.action, "target": self.target, "method": method,
               "intent_id": self.id}
        if self.reason:
            ctx["reason"] = self.reason
        if self.execution_id:
            ctx["execution_id"] = self.execution_id
        ctx["source"] = self.source
        # 安全证据进上下文——日志/事件可回答"是否视觉确认/证据/风险"
        if self.evidence_id:
            ctx["evidence_id"] = self.evidence_id
        ctx["risk"] = self.risk
        return ctx
