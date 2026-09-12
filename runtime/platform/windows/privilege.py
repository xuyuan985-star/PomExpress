"""Privilege（Sprint D-6/7：权限检测与提权——启动即明确，不在点击期才发现）。"""
import ctypes
import sys


def init_dpi():
    """DPI context 统一初始化——所有入口第一行调用。

    SetProcessDPIAware 必须在任何 GDI/窗口 API 之前（import 期可能已读窗口）。
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def is_admin():
    """当前进程是否为管理员（UIPI 拦截 SendInput 的解除条件之一）。"""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def require_admin():
    """硬门槛：非管理员直接抛出（调用方在启动路径处理）。"""
    if not is_admin():
        raise PermissionError("需要管理员权限（UIPI 会拦截 SendInput）；"
                              "请以管理员身份重新启动")
    return True


def relaunch_as_admin(argv=None, wait=True):
    """提权重启自身（D7 UAC 流程）：runas 启动 → 当前进程退出。

    返回 True 表示已发起提权（调用方应退出）。
    """
    import os
    argv = argv or sys.argv
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable,
        " ".join(f'"{a}"' for a in argv), os.getcwd(), 1)
    return result > 32    # ShellExecute 返回值 >32 = 成功
