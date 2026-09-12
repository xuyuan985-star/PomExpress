"""统一资源生命周期管理（轻量）。

进程级注册表 + atexit 清理——线程/子进程/句柄等长生命周期资源
统一登记，退出时保证释放（防长时间运行句柄/内存泄漏）。
"""
import atexit
import logging
import threading

_log = logging.getLogger("runtime.resource")
_lock = threading.Lock()
_cleanups = []    # [(name, callable)]


def register(name, cleanup):
    """注册退出清理回调。name 用于去重（重复注册只保留最后）。"""
    with _lock:
        for i, (n, _) in enumerate(_cleanups):
            if n == name:
                _cleanups[i] = (name, cleanup)
                return
        _cleanups.append((name, cleanup))


def unregister(name):
    with _lock:
        _cleanups[:] = [(n, c) for n, c in _cleanups if n != name]


def _shutdown():
    """atexit 清理：倒序执行（后注册的先清理），单次异常不阻断。"""
    with _lock:
        pending = list(reversed(_cleanups))
    for name, cleanup in pending:
        try:
            cleanup()
        except Exception:
            _log.exception("resource cleanup failed: %s", name)


atexit.register(_shutdown)


class ResourceMonitor(threading.Thread):
    """资源占用监控——CPU/内存异常时告警日志（长时间运行守护）。

    cpu_warn：进程 CPU 百分比告警阈值；mem_warn：进程内存 MB 告警阈值。
    """

    def __init__(self, interval=10.0, cpu_warn=90.0, mem_warn=2048,
                 on_warn=None):
        super().__init__(daemon=True)
        self.interval = interval
        self.cpu_warn = cpu_warn
        self.mem_warn = mem_warn
        self.on_warn = on_warn or (
            lambda msg: _log.warning(msg))
        self._stop = threading.Event()
        self._history = []    # 最近采样（排障用）
        self._last_cpu_times = None
        self._last_wall = None

    def stop(self):
        self._stop.set()

    def run(self):
        import time
        while not self._stop.is_set():
            time.sleep(self.interval)
            try:
                self._sample()
            except Exception:
                _log.exception("resource monitor sample failed")

    def _sample(self):
        import os
        import time
        cpu = mem = None
        try:
            import psutil
            p = psutil.Process(os.getpid())
            cpu = p.cpu_percent(interval=0)
            mem = p.memory_info().rss / (1024 * 1024)
        except Exception:
            # 无 psutil 降级：用 ctypes 取进程内存
            try:
                import ctypes
                class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                    _fields_ = [("cb", ctypes.c_ulong),
                                ("PageFaultCount", ctypes.c_ulong),
                                ("PeakWorkingSetSize", ctypes.c_size_t),
                                ("WorkingSetSize", ctypes.c_size_t),
                                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                                ("PagefileUsage", ctypes.c_size_t),
                                ("PeakPagefileUsage", ctypes.c_size_t)]
                c = PROCESS_MEMORY_COUNTERS()
                c.cb = ctypes.sizeof(c)
                if ctypes.windll.psapi.GetProcessMemoryInfo(
                        ctypes.windll.kernel32.GetCurrentProcess(),
                        ctypes.byref(c), c.cb):
                    mem = c.WorkingSetSize / (1024 * 1024)
            except Exception:
                pass
        sample = {"cpu": cpu, "mem_mb": mem,
                  "time": time.strftime("%H:%M:%S")}
        self._history.append(sample)
        self._history = self._history[-60:]
        if cpu is not None and cpu > self.cpu_warn:
            self.on_warn(f"[ResourceMonitor] CPU {cpu:.0f}% 超阈值 {self.cpu_warn}%")
        if mem is not None and mem > self.mem_warn:
            self.on_warn(f"[ResourceMonitor] 内存 {mem:.0f}MB 超阈值 {self.mem_warn}MB")

    def summary(self):
        return list(self._history)
