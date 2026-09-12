"""模板点击：带超时的反复尝试定位并点击。

参考 外部参考实现 img.py 的 click_target。
"""
import logging
import time
from typing import Callable, Optional

import cv2
import numpy as np

log = logging.getLogger("runtime.scene.click")


def click_target(
    screen: np.ndarray,
    template: np.ndarray,
    click_fn: Callable[[int, int], bool],
    timeout: float = 10.0,
    threshold: float = 0.8,
    interval: float = 0.3,
    poll_fn: Optional[Callable[[], np.ndarray]] = None,
) -> tuple[bool, str]:
    """在 timeout 内反复尝试定位模板并点击。

    Args:
        screen: 当前屏幕截图（仅用于首次匹配）
        template: 模板图像
        click_fn: 点击函数 (x, y) -> bool
        timeout: 总超时秒数
        threshold: 匹配阈值
        interval: 轮询间隔秒数
        poll_fn: 可选的截图函数，每次重试时调用（None 则只用传入的 screen）

    Returns:
        (success, message) 元组：
        - success: True 如果点击成功
        - message: 结果描述（'clicked', 'timeout', 'no_match', 'click_failed'）
    """
    if template is None:
        return False, "no_template"

    deadline = time.monotonic() + timeout
    attempts = 0
    last_confidence = 0.0

    while time.monotonic() < deadline:
        attempts += 1

        # 轮询新截图（如果提供了 poll_fn）
        if poll_fn is not None:
            try:
                screen = poll_fn()
            except Exception as e:
                log.warning("poll_fn failed: %s: %s", type(e).__name__, e)
                return False, "poll_failed"

        if screen is None:
            return False, "no_screen"

        # 模板匹配
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        last_confidence = max_val

        if max_val >= threshold:
            # 计算点击中心（模板中心）
            th, tw = template.shape[:2]
            cx = max_loc[0] + tw // 2
            cy = max_loc[1] + th // 2

            log.info("template found at attempt %d (confidence=%.3f), clicking (%d, %d)",
                     attempts, max_val, cx, cy)

            # 执行点击
            try:
                success = click_fn(cx, cy)
                if success:
                    return True, "clicked"
                else:
                    log.warning("click failed at (%d, %d)", cx, cy)
                    return False, "click_failed"
            except Exception as e:
                log.warning("click_fn exception: %s: %s", type(e).__name__, e)
                return False, "click_exception"

        time.sleep(interval)

    log.warning("click timeout after %.1fs (%d attempts, best confidence=%.3f)",
                timeout, attempts, last_confidence)
    return False, "timeout"
