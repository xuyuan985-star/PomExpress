"""小地图箭头朝向测量与角差计算。

算法依据：外部参考实现 `get_angle.py:31-77` 与 `handle.py:448-463` 的
语义等价实现。流程：
1. `take_arrow(screenshot)` 截取小地图箭头区域并做 HSV 青色掩膜。
2. `get_angle(screenshot)` 从掩膜中 `findContours`，要求恰好 1 个轮廓，
   `approxPolyDP` 简化后取最远顶点，`atan2` 得角度。
3. `cal_ang(arrow_img, arrow_begin_img)` 遍历 0~359° 旋转 `arrow_img`
   与 `arrow_begin_img` 做 `TM_CCORR_NORMED` 匹配，取最大相关对应的角度。
   内部对两图做青色掩膜，消除背景干扰。

契约：
- 所有测量函数在数据不可用（无轮廓、多轮廓、面积过小、半径异常）时
  返回明确的不可用状态与原因，不猜测角度值。
- 角度单位：度（degrees），`atan2` 语义（0° 指向 +x，逆时针增加）。
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class AngleResult:
    """测角结果。

    属性：
    - available：是否成功测得角度。False 时 angle 无意义。
    - angle：测得的朝向角度（度，atan2 语义）。available=False 时为 0.0。
    - reason：不可用时的原因描述；available=True 时为空字符串。
    """
    available: bool
    angle: float
    reason: str = ""


# 小地图箭头区域（相对于截图左上角的裁剪框）。地图 参考用
# [117:175, 128:175] = 58x47 区域。此处默认值与参考一致，允许调用方覆盖。
DEFAULT_ARROW_ROI: tuple[int, int, int, int] = (117, 128, 58, 47)


def take_arrow(screenshot: np.ndarray,
               roi: tuple[int, int, int, int] = DEFAULT_ARROW_ROI,
               ) -> np.ndarray:
    """截取小地图箭头区域并做青色掩膜。

    参数：
    - screenshot：完整游戏截图（BGR）。
    - roi：(x, y, w, h) 裁剪框。

    返回：掩膜后的 BGR 图像（仅青色像素保留，其余置黑）。
    """
    x, y, w, h = roi
    crop = screenshot[y:y + h, x:x + w].copy()
    if crop.size == 0:
        return np.zeros((1, 1, 3), dtype=np.uint8)
    return _cyan_mask(crop)


def get_angle(screenshot: np.ndarray,
              roi: tuple[int, int, int, int] = DEFAULT_ARROW_ROI,
              min_area: float = 20.0,
              min_radius: float = 3.0,
              max_radius: float = 30.0) -> AngleResult:
    """从小地图截图测量人物朝向角度。

    算法：
    1. 裁剪 roi 区域，HSV 青色掩膜。
    2. `findContours` 保留外部轮廓。要求恰好 1 个轮廓，否则返回不可用。
    3. `approxPolyDP` 简化多边形。
    4. 检查面积（>= min_area）与最远点相对中心的距离
       （min_radius <= r <= max_radius），不合理则返回不可用。
    5. 最远顶点相对轮廓中心 `atan2` 得角度。

    契约：不可用时不猜测角度值，附原因。

    参数：
    - screenshot：完整游戏截图（BGR）。
    - roi：小地图箭头区域 (x, y, w, h)。
    - min_area：轮廓最小面积（像素平方），过滤噪点。
    - min_radius：最远点到中心的距离下限（像素）。
    - max_radius：最远点到中心的距离上限（像素）。

    返回 AngleResult。
    """
    arrow = take_arrow(screenshot, roi)
    hsv = cv2.cvtColor(arrow, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([78, 200, 200], dtype=np.uint8),
                        np.array([99, 255, 255], dtype=np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)
    if len(contours) != 1:
        return AngleResult(False, 0.0,
                           f"contours={len(contours)} (need exactly 1)")
    contour = contours[0]
    peri = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, 0.03 * peri, True)
    if len(approx) < 3:
        return AngleResult(False, 0.0, f"approx_vertices={len(approx)}")
    area = cv2.contourArea(contour)
    if area < min_area:
        return AngleResult(False, 0.0, f"area={area:.1f} < min_area={min_area}")
    pts = approx[:, 0, :]
    center = np.mean(pts, axis=0)
    distances = np.linalg.norm(pts - center, axis=1)
    if distances.max() < min_radius:
        return AngleResult(False, 0.0,
                           f"max_dist={distances.max():.1f} < min_radius")
    if distances.max() > max_radius:
        return AngleResult(False, 0.0,
                           f"max_dist={distances.max():.1f} > max_radius")
    furthest_idx = int(np.argmax(distances))
    fx, fy = pts[furthest_idx]
    dx = fx - center[0]
    dy = fy - center[1]
    angle = float(np.degrees(np.arctan2(dy, dx)))
    angle = float(np.around(angle, 2))
    return AngleResult(True, angle, "")


def cal_ang(arrow_img: np.ndarray, arrow_begin_img: np.ndarray) -> float:
    """计算两箭头图之间的角度差（旋转模板匹配）。

    算法（与 地图 `handle.py:448-463` 语义一致）：遍历 i ∈ [0, 359]，
    把 arrow_img 旋转 i 度后与 arrow_begin_img 做 `TM_CCORR_NORMED`
    匹配，取最大相关对应的 i。内部对两图做青色掩膜以消除背景干扰。

    参数：
    - arrow_img：待比较箭头图（BGR）——当前视角。
    - arrow_begin_img：基准箭头图（BGR）——录制初始朝向。

    返回：角度差（度，[0, 359]）——旋转 arrow_img 与 arrow_begin_img
    对齐所需的旋转量。
    """
    masked_img = _cyan_mask(arrow_img)
    masked_begin = _cyan_mask(arrow_begin_img)
    best_val = -1.0
    best_angle = 0
    for i in range(360):
        rotated = _rotate_image(masked_img, i)
        if rotated is None:
            continue
        result = cv2.matchTemplate(masked_begin, rotated,
                                    cv2.TM_CCORR_NORMED)
        if result.size == 0:
            continue
        _, max_val, _, _ = cv2.minMaxLoc(result)
        if max_val > best_val:
            best_val = max_val
            best_angle = i
    return float(best_angle)


def _cyan_mask(img: np.ndarray) -> np.ndarray:
    """对图像做青色掩膜（仅保留青色像素，背景置黑）。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([78, 200, 200], dtype=np.uint8),
                        np.array([99, 255, 255], dtype=np.uint8))
    return cv2.bitwise_and(img, img, mask=mask)


def _rotate_image(img: np.ndarray, angle_deg: float) -> np.ndarray | None:
    """绕图像中心旋转。

    与 地图 `image_rotate` 语义一致：使用仿射变换，插值 INTER_LINEAR。
    """
    h, w = img.shape[:2]
    center = (w / 2.0, h / 2.0)
    M = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR)
