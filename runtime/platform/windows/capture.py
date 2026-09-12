"""CaptureManager（Sprint D-3/D-4：多方式截图 + 帧元数据）。

优先级：PrintWindow（普通窗口）→ 前台 MSS（DX 兜底）→ Fail。
DXGI 捕获为增强项（WinRT GraphicsCapture），接入点已预留。
返回 Frame（image + method + hwnd + timestamp + confidence）
报告可知道"这是 PrintWindow 帧还是 mss 猜测区域"。
"""
import time
from dataclasses import dataclass, field


@dataclass
class Frame:
    image: object = None
    method: str = None            # print_window | foreground_mss | dxgi
    hwnd: int = None
    timestamp: float = None
    confidence: float = 0.0       # 截图方式可信度（print_window 高、mss 低）
    quality: str = "ok"           # FrameValidator 输出（黑屏/黑边/...）
    meta: dict = field(default_factory=dict)


# 方式可信度：PrintWindow 真后台捕获 > mss 前台合成猜测
METHOD_CONFIDENCE = {"print_window": 0.95, "foreground_mss": 0.6, "dxgi": 0.98}


class CaptureManager:
    def __init__(self, vision=None):
        self.vision = vision    # ScreenVision（含降级链 + last_quality）

    @staticmethod
    def frame_hash(img):
        """帧内容指纹——失败报告可确认"这张图就是识别用的那张"。"""
        if img is None:
            return None
        import hashlib
        try:
            return hashlib.sha256(img.tobytes()).hexdigest()[:16]
        except Exception:
            return None

    def capture(self, window=None, source_hint=None):
        """捕获游戏窗口帧（带元数据）。失败 → Frame(image=None, quality=fail)。"""
        from runtime.drivers.local.vision import ScreenVision
        vision = self.vision or ScreenVision()
        shot = vision.take_screenshot()     # 内部已有 PrintWindow → mss 降级链
        img, _, _ = shot
        quality = getattr(vision, "last_quality", None)
        method = getattr(quality, "source", None) or "unknown"
        frame = Frame(
            image=img,
            method=method,
            hwnd=(window.hwnd if window is not None else None),
            timestamp=time.time(),
            confidence=METHOD_CONFIDENCE.get(method, 0.5),
            quality=quality.quality if quality is not None else "ok",
            meta={"reason": getattr(quality, "reason", None) if quality else None,
                  "frame_hash": self.frame_hash(img)},    #
        )
        return frame
