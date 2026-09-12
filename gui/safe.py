"""gui_safe：Qt 槽异常保护装饰器——异常不静默吞，进 ErrorCenter 日志。"""
import functools
import logging

logger = logging.getLogger("gui.safe")


def gui_safe(fn):
    """包装 Qt 槽：异常记录 + 弹窗提示（不静默、不拖垮事件循环）。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error("slot %s failed: %s", fn.__name__, e)
            try:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(None, "帕姆巡宝 错误",
                                     f"{fn.__name__}: {e}")
            except Exception:
                pass
            return None
    return wrapper
