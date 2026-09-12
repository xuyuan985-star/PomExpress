"""WindowManager（Sprint D-2：不抓错窗口——评分选择，不取第一个）。

现有 find_game_window（可见 + 客户区最大 + 进程过滤 + 500px 下限）已实现
核心；本模块提供显式评分算法与 Frame 级窗口元数据（D4）。
"""
from dataclasses import dataclass, field


@dataclass
class GameWindow:
    hwnd: int = 0
    width: int = 0
    height: int = 0
    visible: bool = False
    score: float = 0.0
    pid: int = None
    extra: dict = field(default_factory=dict)


def score_window(w):
    """评分：可见 +50 / 宽度 >1000 +30 / 高度 >600 +20 / 面积加成。"""
    s = 0.0
    if w.visible:
        s += 50
    if w.width > 1000:
        s += 30
    if w.height > 600:
        s += 20
    s += min(20.0, (w.width * w.height) / 200000.0)    # 面积微调
    return s


def find_best_window(title=None):
    """枚举所有候选 → 评分排序 → 返回最佳 GameWindow（无候选 → None）。"""
    from runtime.win_capture import find_game_window
    info = find_game_window(title)
    if info is None:
        return None
    w, h = info["client"]
    gw = GameWindow(hwnd=info["hwnd"], width=w, height=h,
                    visible=bool(info.get("visible", True)),
                    pid=info.get("pid"),
                    extra={"title": info.get("title")})
    gw.score = score_window(gw)
    return gw
