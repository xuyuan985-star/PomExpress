"""合成箭头图像：为无游戏窗口环境生成已知角度的青色箭头。

用途：测试 `get_angle` 与 `cal_ang`。生成 64x64 BGR 图像，
在中心绘制青色三角形箭头，箭头顶点指向给定角度。

坐标系约定（与 OpenCV 一致）：
- x 轴向右，y 轴向下。
- 角度 0° 表示指向右侧（+x），逆时针增加（atan2 语义）。
"""

from __future__ import annotations

import numpy as np


def _arrow_polygon(cx: int, cy: int, length: int, half_width: int,
                   angle_deg: float) -> np.ndarray:
    """生成三角箭头顶点坐标。

    参数：
    - cx, cy：箭头中心（像素）。
    - length：箭头长度（像素）。
    - half_width：箭头半宽（像素）。
    - angle_deg：箭头指向（度，atan2 语义）。

    返回 3x2 float32 数组（三个顶点坐标），保留浮点精度以避免
    整数舍入导致的角度偏差。
    """
    theta = np.deg2rad(angle_deg)
    tip_x = cx + length * np.cos(theta)
    tip_y = cy + length * np.sin(theta)
    perp_x = -np.sin(theta)
    perp_y = np.cos(theta)
    base_x = cx - 0.3 * length * np.cos(theta)
    base_y = cy - 0.3 * length * np.sin(theta)
    left = (base_x + half_width * perp_x, base_y + half_width * perp_y)
    right = (base_x - half_width * perp_x, base_y - half_width * perp_y)
    return np.array([[tip_x, tip_y],
                     [left[0], left[1]],
                     [right[0], right[1]]], dtype=np.float32)


def render_arrow(angle_deg: float, size: int = 64,
                 background: tuple[int, int, int] = (100, 100, 100),
                 arrow_color: tuple[int, int, int] = (255, 255, 0),
                 center: tuple[int, int] | None = None,
                 arrow_length: int = 22,
                 arrow_half_width: int = 6) -> np.ndarray:
    """渲染合成箭头图。

    参数：
    - angle_deg：箭头指向（度，atan2 语义）。
    - size：图像边长（正方形）。
    - background：背景 BGR 颜色。默认灰色 (100,100,100)，HSV 中
      H=0/S=0，不会干扰青色掩膜。
    - arrow_color：箭头 BGR 颜色。默认 (255,255,0) 为青色，HSV 中
      H=90/S=255/V=255，落在掩膜范围 [78,99] 内。
    - center：箭头中心；默认图像中心。
    - arrow_length：箭头长度（像素）。
    - arrow_half_width：箭头半宽（像素）。

    返回 BGR 图像（uint8，size x size x 3）。
    """
    import cv2
    if center is None:
        center = (size // 2, size // 2)
    img = np.full((size, size, 3), background, dtype=np.uint8)
    poly = _arrow_polygon(center[0], center[1], arrow_length,
                          arrow_half_width, angle_deg)
    cv2.fillPoly(img, [poly.astype(np.int32)], arrow_color)
    return img


def render_arrow_with_ring(angle_deg: float, size: int = 64) -> np.ndarray:
    """渲染带小地图圆环的箭头图（更接近真实场景）。

    背景包含一圈浅灰色圆环，模拟小地图外框；中心绘制青色箭头。
    """
    import cv2
    img = np.full((size, size, 3), (100, 100, 100), dtype=np.uint8)
    cv2.circle(img, (size // 2, size // 2), size // 2 - 2,
               (180, 180, 180), thickness=2)
    poly = _arrow_polygon(size // 2, size // 2, 22, 6, angle_deg)
    cv2.fillPoly(img, [poly.astype(np.int32)], (255, 255, 0))
    return img


def cyan_mask_range() -> tuple[np.ndarray, np.ndarray]:
    """返回青色箭头的 HSV 掩膜上下界。

    与 地图 参考一致：H ∈ [78, 99]，S 下界 200 用于过滤小地图圆弧。
    """
    return (np.array([78, 200, 200], dtype=np.uint8),
            np.array([99, 255, 255], dtype=np.uint8))
