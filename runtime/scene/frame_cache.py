"""帧缓存：同一轮匹配内复用截图。

避免连续多次模板匹配重复截屏。
参考 外部参考实现 img.py 的 temp_screenshot。
"""
import logging
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

import cv2
import numpy as np

log = logging.getLogger("runtime.scene.frame_cache")


class FrameCache:
    """帧缓存器：在同一轮匹配内复用截图。

    用法：
        cache = FrameCache(screenshot_fn)
        cache.start()  # 截一次图
        tmpl1 = cache.match(template1)  # 复用缓存
        tmpl2 = cache.match(template2)  # 复用缓存
        cache.end()     # 释放缓存
    """

    def __init__(self, screenshot_fn: Optional[Callable[[], np.ndarray]] = None):
        """初始化帧缓存。

        Args:
            screenshot_fn: 截图函数（可选，None 则需手动 set_frame）
        """
        self._screenshot_fn = screenshot_fn
        self._frame: Optional[np.ndarray] = None
        self._match_count = 0

    @property
    def frame(self) -> Optional[np.ndarray]:
        """当前缓存的帧。"""
        return self._frame

    @property
    def match_count(self) -> int:
        """当前轮次的匹配次数。"""
        return self._match_count

    def start(self) -> bool:
        """开始缓存：截取一帧。

        Returns:
            True 如果成功截取，False 如果失败
        """
        if self._screenshot_fn is None:
            log.warning("no screenshot_fn provided, cannot start cache")
            return False

        try:
            self._frame = self._screenshot_fn()
            self._match_count = 0
            log.debug("frame cached (%dx%d)", *self._frame.shape[:2])
            return True
        except Exception as e:
            log.warning("screenshot failed: %s: %s", type(e).__name__, e)
            return False

    def set_frame(self, frame: np.ndarray) -> None:
        """手动设置缓存帧（当没有 screenshot_fn 时）。"""
        self._frame = frame
        self._match_count = 0

    def get_frame(self) -> Optional[np.ndarray]:
        """获取缓存帧。"""
        return self._frame

    def end(self) -> None:
        """结束缓存：释放帧。"""
        self._frame = None
        log.debug("frame cache ended (%d matches)", self._match_count)

    def __enter__(self) -> 'FrameCache':
        """作为上下文管理器使用。"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器退出时释放缓存。"""
        self.end()


def cached_match(
    cache: FrameCache,
    template: np.ndarray,
    method: int = None,
    threshold: float = 0.8,
) -> Optional[tuple[float, tuple[int, int]]]:
    """使用缓存帧进行模板匹配。

    Args:
        cache: FrameCache 实例（必须已 start）
        template: 模板图像
        method: OpenCV 匹配方法（None 则用 TM_CCOEFF_NORMED）
        threshold: 匹配阈值

    Returns:
        (confidence, location) 如果匹配成功，否则 None
    """
    if cache.frame is None:
        log.warning("cache is empty, call start() first")
        return None

    cache._match_count += 1

    if method is None:
        method = cv2.TM_CCOEFF_NORMED

    result = cv2.matchTemplate(cache.frame, template, method)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val >= threshold:
        log.debug("match success: confidence=%.3f at %s", max_val, max_loc)
        return max_val, max_loc

    log.debug("match failed: confidence=%.3f (threshold=%.3f)", max_val, threshold)
    return None
