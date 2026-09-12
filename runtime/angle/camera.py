"""视角闭环的可注入相机接口。

设计要点：
- 抽象出 `take_arrow()` 与 `mouse_move(dx)` 两个最小能力，使校准与回正
  可在无游戏窗口的环境下用模拟相机验证。
- 真实实现（RealCamera）封装 win32 鼠标事件与小地图截图；本仓库当前
  不接线，仅定义接口与模拟实现，便于后续接入。
- 模拟实现（SimulatedCamera）维护一个浮点角度状态，每次 mouse_move
  按 `factor` 缩放后累加，用于合成图与回正收敛性验证。

单位约定：
- 角度：度（degrees），范围 [0, 360) 或带符号 [-180, 180]。
- dx：像素（相对位移），正值为顺时针旋转。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class AngleCamera(Protocol):
    """视角闭环相机接口。

    契约：
    - `take_arrow()` 返回小地图箭头区域的 BGR 图像（numpy 数组，含青色
      箭头像素与背景）。测试中返回合成图。
    - `mouse_move(dx)` 按像素位移旋转视角。`dx > 0` 顺时针，`dx < 0`
      逆时针。返回值无意义（副作用式 API）。
    - `move_forward(duration)` 向前移动一小段时间，使角色朝向更新为
      当前视角方向。仅校准与回正流程需要（让箭头指向与视角同步）。
    """

    def take_arrow(self) -> np.ndarray:
        """返回当前小地图箭头区域的 BGR 图像。"""

    def mouse_move(self, dx: float) -> None:
        """按像素位移旋转视角（副作用式）。"""

    def move_forward(self, duration: float = 0.01) -> None:
        """向前移动一小段时间，让箭头指向与视角同步。"""


class SimulatedCamera:
    """模拟相机：用浮点角度状态 + 可注入的缩放因子。

    用途：测试 `set_angle` 校准系数求解与 `reset_view` 收敛性。
    测试代码不告知被测模块 `factor` 值——被测模块通过实际测量反推。

    参数：
    - factor：像素位移到角度的换算因子。真实游戏中此值受鼠标 DPI、
      Windows 指针速度、分辨率、FOV 影响。
    - pixel_to_angle：鼠标像素位移到角度的换算比例。默认 1.0（1 像素
      转 1 度 * factor），与测试模型 `angle += dx * factor` 对齐。
    """

    def __init__(self, initial_angle: float = 0.0, factor: float = 1.0,
                 pixel_to_angle: float = 1.0):
        self.initial_angle = initial_angle
        self.factor = factor
        self.pixel_to_angle = pixel_to_angle
        self.angle = initial_angle
        self._moves: list[float] = []

    def take_arrow(self) -> np.ndarray:
        """返回当前角度的合成箭头图（青色箭头 + 灰背景）。

        返回 64x64 BGR 图像，箭头指向与 `self.angle` 一致。
        """
        from runtime.angle.synthetic import render_arrow
        return render_arrow(self.angle)

    def mouse_move(self, dx: float) -> None:
        """按像素位移旋转视角。

        `dx` 为像素；`dx * pixel_to_angle * factor` 为实际转角。
        """
        self._moves.append(dx)
        self.angle = (self.angle + dx * self.pixel_to_angle * self.factor) % 360.0

    def move_forward(self, duration: float = 0.01) -> None:
        """模拟相机不做实际移动，接口签名保留。"""
        return None

    def reset_angle(self, angle: float) -> None:
        """测试辅助：直接设置角度（跳过 mouse_move 换算）。"""
        self.angle = angle % 360.0


def create_simulated_camera(factor: float, initial_angle: float = 0.0,
                            pixel_to_angle: float = 1.0) -> SimulatedCamera:
    """工厂：创建指定 factor 的模拟相机。

    测试代码可通过此工厂创建相机，然后传给 `set_angle` / `reset_view`。
    被测模块不感知 factor 值——通过实际测量反推。
    """
    return SimulatedCamera(initial_angle=initial_angle, factor=factor,
                          pixel_to_angle=pixel_to_angle)
