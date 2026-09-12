"""场景界面判定：等待指定界面出现的复合封装。

参考 外部参考实现 img.py 的 on_interface / on_main_interface。
使用模板匹配循环重试，支持 check_list + timeout + threshold。
"""
import logging
import time
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger("runtime.scene.interface")


def on_interface(
    screen: np.ndarray,
    templates: list[np.ndarray],
    timeout: float = 30.0,
    threshold: float = 0.8,
    interval: float = 0.5,
    poll_fn=None,
) -> bool:
    """等待指定界面出现。

    Args:
        screen: 当前屏幕截图 (BGR ndarray)
        templates: 模板列表，任一匹配即认为界面已出现
        timeout: 总超时秒数
        threshold: 匹配阈值 (0-1)
        interval: 轮询间隔秒数
        poll_fn: 可选的截图函数，返回新的 screen（None 则只用传入的 screen）

    Returns:
        True 如果界面在 timeout 内出现，否则 False

    Raises:
        ValueError: 如果 templates 为空
    """
    if not templates:
        raise ValueError("templates must not be empty")

    deadline = time.monotonic() + timeout
    attempts = 0

    while time.monotonic() < deadline:
        attempts += 1

        # 轮询新截图（如果提供了 poll_fn）
        if poll_fn is not None:
            try:
                screen = poll_fn()
            except Exception as e:
                log.warning("poll_fn failed: %s: %s", type(e).__name__, e)
                return False

        # 检查黑屏（快速跳过无效帧）
        if _is_invalid_frame(screen):
            log.debug("attempt %d: invalid frame (black/white/low variance)", attempts)
            time.sleep(interval)
            continue

        # 逐个模板匹配
        for i, tmpl in enumerate(templates):
            result = cv2.matchTemplate(screen, tmpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)

            if max_val >= threshold:
                log.info("interface found at attempt %d (template %d, confidence=%.3f)",
                         attempts, i, max_val)
                return True

        time.sleep(interval)

    log.warning("interface not found within %.1fs (%d attempts)", timeout, attempts)
    return False


def _is_invalid_frame(screen: np.ndarray) -> bool:
    """快速检查帧是否无效（纯黑/纯白/低方差）。"""
    if screen is None or screen.size == 0:
        return True

    gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY) if len(screen.shape) == 3 else screen
    mean = np.mean(gray)
    std = np.std(gray)

    # 纯黑或纯白
    if mean < 10 or mean > 245:
        return True

    # 低方差（几乎单色）
    if std < 5:
        return True

    return False
