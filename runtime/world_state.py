"""WorldState（Sprint E-5：Agent 世界状态——Planner 决策输入）。

Observation → WorldState（信念浓缩）：当前房间/UI/进度。
事件化更新（E9：禁止直接改 state，必须经 update 产生变更记录）。
"""
from dataclasses import dataclass, field


@dataclass
class WorldState:
    room: str = None             # room_A / base_zone（knowledge id）
    ui: str = None               # game|map|loading|menu|dialogue|combat|shop
    completed: list = field(default_factory=list)    # 已完成目标
    confidence: float = 0.0

    def update(self, observation):
        """从 Observation 更新；返回变更描述（None = 无变化）。"""
        changes = []
        if observation is None:
            return None
        new_room = getattr(observation, "room", None)
        if new_room and new_room != self.room:
            changes.append(f"room:{self.room}->{new_room}")
            self.room = new_room
        new_ui = getattr(observation, "ui_state", None)
        if new_ui and new_ui != self.ui:
            changes.append(f"ui:{self.ui}->{new_ui}")
            self.ui = new_ui
        conf = getattr(observation, "confidence", 0.0)
        self.confidence = max(self.confidence, conf)
        return changes or None

    def to_context(self):
        return {"room": self.room, "ui": self.ui,
                "completed": self.completed, "confidence": round(self.confidence, 2)}
