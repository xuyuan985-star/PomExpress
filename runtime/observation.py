"""Observation（第 7 步：Observer 输出——"我看到了什么"）。

Observer 只回答看到什么，不产行动（与 约束一致）。
含与 ObservationStore.ObservationRecord 的互转（记录/执行链共用）。
"""
from dataclasses import dataclass, field


@dataclass
class Observation:
    """视觉观察结果（各观察器统一输出）。"""

    room: str = None                   # 房间判定
    ui_state: str = None               # game|map|loading|menu|dialogue|combat
    text: list = field(default_factory=list)     # OCR 文本行
    entities: list = field(default_factory=list)    # [{id, bbox, confidence}] 目标定位
    confidence: float = 0.0
    source: str = "unknown"            # ocr | template | fake | frame_validator
    screenshot: str = None             # 帧路径（证据/复盘）
    frame_id: str = None               # 帧标识（观察-执行原子性 token 基础）
    frame_confidence: float = None     # 截图来源可信度（PrintWindow 0.95/mss 0.6）

    def to_context(self):
        return {
            "room": self.room, "ui_state": self.ui_state,
            "text": self.text[:10], "entities": [
                {"id": e.get("id"), "confidence": e.get("confidence")}
                for e in self.entities[:5]],
            "confidence": self.confidence, "source": self.source,
            "frame_id": self.frame_id,
        }
