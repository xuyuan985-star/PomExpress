"""FrameValidator： -G 截图质量检测——"成功截图 ≠ 正确截图"。

PrintWindow/mss 成功返回不代表内容是游戏画面（可能截到 Edge/桌面/黑屏）。
本模块做低成本结构校验（坏帧进识别前拦截）：
- 全黑 / 全白 / 全零
- 黑边比例（上下左右纯色带面积占比）——窗口化 DX 截图常见黑边
内容级校验（OCR UID / 游戏 logo / UI 特征）依赖游戏内可标定特征，
留真机 G4 阶段按 knowledge 包声明接入。
"""
from dataclasses import dataclass, field


@dataclass
class CaptureResult:
    """截图验证结果：image 原样保留，quality/reason 供调用方决策。"""
    image: object = None
    source: str = None        # print_window | foreground_mss
    quality: str = "ok"       # ok | black | white | heavy_black_borders
    reason: str = None        # 不通过原因（人类可读）
    meta: dict = field(default_factory=dict)


class FrameValidator:
    """-G：结构级帧校验。validate(img) → CaptureResult。"""

    def __init__(self, border_threshold=0.30, strip=0.02,
                 expected_size=None, static_epsilon=1.0):
        # border_threshold：黑边占整帧面积比例上限；strip：边缘取样条宽度
        self.border_threshold = border_threshold
        self.strip = strip
        self.expected_size = expected_size    # Sprint B：期望 (w, h)，不符 → size 异常
        self.static_epsilon = static_epsilon    # 连续帧平均绝对差阈值

    def check_static(self, frames):
        """#SB：连续帧静态检测——3 帧近乎零差异 = 卡死/截错/隐藏窗口。

        frames: [ndarray_gray, ...] 至少 2 帧。返回 (is_static, max_diff)。
        """
        import numpy as np
        if len(frames) < 2:
            return False, 0.0
        diffs = []
        prev = None
        for f in frames:
            arr = np.asarray(f)
            if arr.ndim == 3:
                arr = arr.mean(axis=2)
            if prev is not None and arr.shape == prev.shape:
                diffs.append(float(np.abs(arr.astype(float) - prev.astype(float)).mean()))
            prev = arr
        if not diffs:
            return False, 0.0
        max_diff = max(diffs)
        return max_diff < self.static_epsilon, round(max_diff, 2)

    def validate(self, img, source=None):
        import numpy as np
        arr = np.asarray(img.convert("L")) if hasattr(img, "convert") else np.asarray(img)
        if arr.ndim != 2:
            arr = arr.mean(axis=2) if arr.ndim == 3 else arr
        if arr.size == 0:
            return CaptureResult(image=img, source=source, quality="black",
                                 reason="empty frame")
        h, w = arr.shape
        # Sprint B：尺寸异常（期望分辨率不符 → 截错窗口/缩放异常）
        if self.expected_size is not None:
            ew, eh = self.expected_size
            if (w, h) != (ew, eh):
                return CaptureResult(image=img, source=source, quality="size_mismatch",
                                     reason=f"size {w}x{h} != expected {ew}x{eh}")
        dark = arr < 32
        black_ratio = float(dark.mean())
        if black_ratio > 0.99:
            return CaptureResult(image=img, source=source, quality="black",
                                 reason=f"full dark {black_ratio:.2f}")
        light = arr > 224
        white_ratio = float(light.mean())
        if white_ratio > 0.99:
            return CaptureResult(image=img, source=source, quality="white",
                                 reason=f"full white {white_ratio:.2f}")
        s = max(1, int(min(h, w) * self.strip))
        strips = (arr[:s, :], arr[-s:, :], arr[:, :s], arr[:, -s:])
        edge_dark = [float((chunk < 32).mean()) for chunk in strips]
        worst = max(edge_dark)
        if worst > 0.8 and black_ratio < 0.99:
            return CaptureResult(
                image=img, source=source, quality="heavy_black_borders",
                reason=f"edge dark {worst:.2f} black_ratio {black_ratio:.2f}",
                meta={"edge_dark": [round(v, 2) for v in edge_dark]})
        return CaptureResult(image=img, source=source, quality="ok", reason="ok",
                             meta={"black_ratio": round(black_ratio, 3)})
