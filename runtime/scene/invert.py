"""阴阳反转兜底：匹配度低时自动尝试反色模板。

参考 外部参考实现 img.py 的 img_bitwise_check。
"""
import logging
from typing import Optional, Tuple

import cv2
import numpy as np

log = logging.getLogger("runtime.scene.invert")


def img_bitwise_check(
    screen: np.ndarray,
    template: np.ndarray,
    threshold: float = 0.8,
    invert_threshold: float = 0.5,
) -> Optional[Tuple[float, tuple[int, int], bool]]:
    """模板匹配，匹配度低时自动尝试反色模板。

    Args:
        screen: 屏幕截图 (BGR ndarray)
        template: 模板图像
        threshold: 正常匹配阈值
        invert_threshold: 触发反色尝试的阈值（低于此值时尝试反色）

    Returns:
        (confidence, location, was_inverted) 如果匹配成功：
        - confidence: 匹配置信度
        - location: 匹配位置 (x, y)
        - was_inverted: 是否使用了反色模板
        None 如果两种都未匹配
    """
    if screen is None or template is None:
        log.warning("screen or template is None")
        return None

    # 第一次尝试：原模板
    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val >= threshold:
        log.debug("normal match: confidence=%.3f at %s", max_val, max_loc)
        return max_val, max_loc, False

    log.debug("normal match failed: confidence=%.3f (threshold=%.3f)", max_val, threshold)

    # 如果置信度太低，尝试反色模板
    if max_val < invert_threshold:
        log.info("trying inverted template (normal confidence=%.3f < %.3f)",
                 max_val, invert_threshold)

        # 反色模板
        inverted_template = cv2.bitwise_not(template)

        # 再次匹配
        result_inv = cv2.matchTemplate(screen, inverted_template, cv2.TM_CCOEFF_NORMED)
        _, max_val_inv, _, max_loc_inv = cv2.minMaxLoc(result_inv)

        if max_val_inv >= threshold:
            log.info("inverted match: confidence=%.3f at %s", max_val_inv, max_loc_inv)
            return max_val_inv, max_loc_inv, True

        log.warning("both normal and inverted match failed (normal=%.3f, inverted=%.3f)",
                    max_val, max_val_inv)
        return None

    # 置信度在中间范围，不尝试反色（可能只是稍微低一点）
    log.debug("confidence in middle range (%.3f), skipping invert", max_val)
    return None
