"""回放前闭环视角回正。

算法依据：外部参考实现 `handle.py:424-446 handle_view_reset` 的语义等价实现。

流程：
1. `move_forward(0.01)` 让箭头指向前方（视角方向）。
2. 使用传入的 `baseline_arrow` 或当前 `take_arrow()` 作为基准。
3. 循环最多 max_iterations 轮：
   - `arrow_temp = take_arrow()`。
   - `ang = cal_ang(arrow_temp, arrow_begin)`（当前箭头相对基准的角度差）。
   - `sub = 360 - ang`，归一化到 ±180。
   - `dx = sub * multi_num` 发射校正位移（sub 为逆时针校正量）。
   - `move_forward(0.01)` 让箭头更新。
   - 若 `abs(sub) <= tolerance` 则收敛退出。
4. 超时或达到最大迭代次数仍未收敛 → 返回失败状态。

契约：
- 失败必须返回明确状态 + 可见日志（不得静默继续）。
- 迭代次数与超时均有上限。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from runtime.angle.camera import AngleCamera
from runtime.angle.orientation import cal_ang


@dataclass(frozen=True)
class ResetResult:
    """回正结果。

    属性：
    - ok：是否成功回正。
    - iterations：实际执行的迭代次数。
    - final_angle_error：最终角度误差（度）。ok=False 时可能 > tolerance。
    - reason：失败原因；ok=True 时为空字符串。
    """
    ok: bool
    iterations: int
    final_angle_error: float
    reason: str = ""


def reset_view(camera: AngleCamera,
               multi_num: float,
               baseline_arrow: np.ndarray | None = None,
               max_iterations: int = 4,
               timeout_seconds: float = 5.0,
               tolerance: float = 1.0,
               iteration_delay: float = 0.6) -> ResetResult:
    """回放前闭环视角回正。

    迭代校正：每轮测量当前角度差，发射校正位移，最多 max_iterations 轮。

    参数：
    - camera：AngleCamera 实现。
    - multi_num：校准系数（来自 set_angle）。
    - baseline_arrow：基准箭头图（录制初始朝向）。None 时使用当前
      `take_arrow()` 作为基准。
    - max_iterations：最大迭代次数。
    - timeout_seconds：总超时时间（秒）。
    - tolerance：收敛阈值（度），abs(sub) <= tolerance 即收敛。
    - iteration_delay：每轮之间的等待时间（秒），让游戏渲染更新。

    返回 ResetResult。失败时附原因与最终误差。
    """
    camera.move_forward(0.01)
    arrow_begin = baseline_arrow if baseline_arrow is not None else camera.take_arrow()
    start_time = time.time()
    last_error = 360.0

    for iteration in range(max_iterations):
        if time.time() - start_time > timeout_seconds:
            return ResetResult(False, iteration, last_error,
                               f"timeout: {timeout_seconds}s exceeded")
        arrow_temp = camera.take_arrow()
        ang = cal_ang(arrow_temp, arrow_begin)
        sub = 360.0 - ang
        sub = (sub + 180.0) % 360.0 - 180.0
        last_error = abs(sub)
        if last_error <= tolerance:
            return ResetResult(True, iteration + 1, last_error, "")
        dx = sub * multi_num
        camera.mouse_move(dx)
        camera.move_forward(0.01)
        if iteration < max_iterations - 1:
            time.sleep(iteration_delay)

    return ResetResult(False, max_iterations, last_error,
                       f"max_iterations={max_iterations} reached, "
                       f"final_error={last_error:.2f}° > tolerance={tolerance}°")
