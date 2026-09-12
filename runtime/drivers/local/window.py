"""Local 窗口桥接（数据内化）：全部转发自研 runtime/win_capture。

可见窗口枚举 + 最大客户区选择（多实例规避）+ 前台激活三级回退
统一实现在 runtime/win_capture.py。
"""


def find_game_window():
    """窗口查找（自研实现见 runtime/win_capture——客户区+进程名过滤更强）。"""
    from runtime.win_capture import find_game_window as _fg
    return _fg()


def set_foreground_with_retry(hwnd, attempts=3):
    """前台激活（自研实现见 runtime/win_capture——三级回退）。"""
    from runtime.win_capture import set_foreground_with_retry as _sf
    return _sf(hwnd)
