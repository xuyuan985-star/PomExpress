"""CoordinateSpace（Sprint D-8：DPI 统一层——业务代码禁止 x/scale 散落）。

Local screenshot_scale_factor 已归一化截图；本模块把"逻辑↔物理"换算
收敛为单一实现，供校准/诊断工具使用（runtime 执行链不接触坐标）。
"""
from dataclasses import dataclass


@dataclass
class CoordinateSpace:
    logical_width: int = 1920
    logical_height: int = 1080
    physical_width: int = 1920
    physical_height: int = 1080
    scale: float = 1.0

    @classmethod
    def from_scale_factor(cls, scale, logical_w=1920, logical_h=1080):
        # D5-B6：half-away-from-zero 取整
        return cls(logical_width=logical_w, logical_height=logical_h,
                   physical_width=round(logical_w * scale),
                   physical_height=round(logical_h * scale),
                   scale=scale)


def logical_to_physical(x, y, space: CoordinateSpace):
    # D5-B6：half-away-from-zero 取整（与 vision.to_absolute 一致）
    return round(x * space.scale), round(y * space.scale)


def physical_to_logical(x, y, space: CoordinateSpace):
    if space.scale <= 0:
        return round(x), round(y)
    return round(x / space.scale), round(y / space.scale)


def screenshot_to_screen(x, y, pos, scale=1.0):
    """截图内坐标 → 绝对屏幕坐标（唯一实现，工具共用）。

    pos = (left, top, right, bottom) 客户区绝对屏幕位置。
    """
    if not scale:
        scale = 1.0
    return (pos[0] + round(x / scale),
            pos[1] + round(y / scale))
