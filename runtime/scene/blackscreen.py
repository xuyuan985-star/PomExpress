"""黑屏检测：截图有效性判定。

纯黑/纯白/低方差视为无效帧。
参考 外部参考实现 utils/blackscreen.py。
"""
import logging
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger("runtime.scene.blackscreen")

# 阈值依据：
# - 纯黑：mean < 10（接近 0）
# - 纯白：mean > 245（接近 255）
# - 低方差：std < 5（几乎单色，无明显内容）
# 这些阈值基于合成图测试验证，正常游戏画面 std 通常在 30-100 之间。
BLACK_MEAN_THRESHOLD = 10.0
WHITE_MEAN_THRESHOLD = 245.0
LOW_STD_THRESHOLD = 5.0


def is_blackscreen(
    screen: np.ndarray,
    black_threshold: float = BLACK_MEAN_THRESHOLD,
    white_threshold: float = WHITE_MEAN_THRESHOLD,
    low_std_threshold: float = LOW_STD_THRESHOLD,
) -> tuple[bool, str]:
    """检查截图是否为无效帧（纯黑/纯白/低方差）。

    Args:
        screen: 屏幕截图 (BGR ndarray)
        black_threshold: 纯黑判定阈值（mean < 此值）
        white_threshold: 纯白判定阈值（mean > 此值）
        low_std_threshold: 低方差判定阈值（std < 此值）

    Returns:
        (is_invalid, reason) 元组：
        - is_invalid: True 表示无效帧
        - reason: 原因字符串（'black', 'white', 'low_variance', 'empty', 或 'ok'）
    """
    if screen is None or screen.size == 0:
        return True, "empty"

    # 转灰度
    if len(screen.shape) == 3:
        gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
    else:
        gray = screen

    mean = float(np.mean(gray))
    std = float(np.std(gray))

    if mean < black_threshold:
        log.debug("black screen detected: mean=%.2f", mean)
        return True, "black"

    if mean > white_threshold:
        log.debug("white screen detected: mean=%.2f", mean)
        return True, "white"

    if std < low_std_threshold:
        log.debug("low variance detected: std=%.2f", std)
        return True, "low_variance"

    return False, "ok"
