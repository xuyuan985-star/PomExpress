"""场景层机制模块：界面判定、黑屏检测、帧缓存、点击超时、反色兜底。

P0-05~P0-10 移植自 外部参考实现 机制，使用合成图验证。
"""
from runtime.scene.interface import on_interface
from runtime.scene.blackscreen import is_blackscreen
from runtime.scene.frame_cache import FrameCache
from runtime.scene.click import click_target
from runtime.scene.invert import img_bitwise_check

__all__ = [
    "on_interface",
    "is_blackscreen",
    "FrameCache",
    "click_target",
    "img_bitwise_check",
]
