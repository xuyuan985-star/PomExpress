"""运行中 QThread 注册表（退出保护）。

用途
    QApplication 退出时，若仍有 QThread 在运行，Qt 析构运行中的线程会导致进程
    异常终止（本环境实测为 0xC0000409）。本模块提供运行中线程的全局登记，
    退出钩子据此等待或告警。

约定
    QThread 子类在 start() 前调用 register_qthread(self)，在 finished 后调用
    unregister_qthread(self)。注册表只记录引用，不持有线程所有权。
"""
from __future__ import annotations

import threading

_ALL_QTHREADS: set[object] = set()
_ALL_QTHREADS_LOCK = threading.Lock()


def register_qthread(thread) -> None:
    """登记一个线程，供退出保护查询。"""
    with _ALL_QTHREADS_LOCK:
        _ALL_QTHREADS.add(thread)


def unregister_qthread(thread) -> None:
    """撤销登记（线程结束后调用）。"""
    with _ALL_QTHREADS_LOCK:
        _ALL_QTHREADS.discard(thread)


def all_running_qthreads() -> list:
    """返回当前仍在运行的已登记线程。"""
    with _ALL_QTHREADS_LOCK:
        return [t for t in _ALL_QTHREADS if t.isRunning()]
