"""像素差分画面变化检测 + nudge 微调（借鉴 GameCLI-Agent pil_images_different）。

点击/按键后快速验证"画面是否真的变化"——星铁动画多、反馈延迟，
SendInput 成功 ≠ 游戏响应。差分失败（画面没动）→ 点偏/被挡 → nudge
微调坐标重试，而不是等 verify 超时。

192x108 灰度降采样：忽略压缩噪声/待机闪烁（pixel_threshold=20）；
fraction_threshold=0.8% 像素变化即视为变化（点击开箱动画远大于此）。
"""
import numpy as np


def _gray_thumbnail(img, size=(192, 108)):
    """灰度降采样（RGB/RGBA PIL → 192x108 float32 灰度）。"""
    import numpy as np
    arr = np.asarray(img.convert("L").resize(size)) if hasattr(img, "convert") \
        else np.asarray(img)
    if arr.ndim == 3:
        arr = arr.mean(axis=2)
    return arr.astype(np.float32)


def images_different(img1, img2, pixel_threshold=20, fraction_threshold=0.008):
    """两帧是否"显著变化"（抄 GameCLI 语义：>阈值像素数占比超 0.8%）。

    返回 (changed, diff_ratio)。异常（空图/尺寸异常）→ (False, 0.0)
    （fail 保守——不因差分误判"变了"）。
    """
    try:
        if img1 is None or img2 is None:
            return False, 0.0
        a = _gray_thumbnail(img1)
        b = _gray_thumbnail(img2)
        if a.shape != b.shape or a.size == 0:
            return False, 0.0
        diff = np.abs(a - b)
        changed_pixels = int((diff > pixel_threshold).sum())
        ratio = changed_pixels / float(a.size)
        return ratio > fraction_threshold, round(ratio, 4)
    except Exception:
        return False, 0.0


def nudge_offsets(step_px=4, max_radius=16):
    """nudge 微调偏移序列（8 方向 + 递增半径——抄 GameCLI 思路）。

    生成 [(dx, dy), ...]：半径 4/8/12/16 的 8 方向偏移（菱形），
    共 32 个候选。用于"点偏了"时微调坐标重试。
    """
    offsets = [(0, 0)]
    for r in range(step_px, max_radius + 1, step_px):
        for dx in range(-r, r + 1, r):
            for dy in range(-r, r + 1, r):
                if dx == 0 and dy == 0:
                    continue
                offsets.append((dx, dy))
    return offsets
