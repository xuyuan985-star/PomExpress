"""SessionWatchdog — S10 Supervisor 轻量版：监控事件流活跃度。

executor 若阻塞（如验证循环无 abort 钩子），事件流静默超过阈值 →
deadlock_detected 事件 + 置位；主循环每 step 检查置位即中断。
独立线程、daemon，随 mission 启停。
"""
import threading
import time


class SessionWatchdog(threading.Thread):
    """S10 Supervisor 轻量版：监控事件流活跃度。

    executor 若阻塞（如验证循环无 abort 钩子），事件流静默超过阈值 →
    deadlock_detected 事件 + 置位；主循环每 step 检查置位即中断。
    独立线程、daemon，随 mission 启停。
    """

    def __init__(self, bus, execution_id, stall_seconds=120):
        super().__init__(daemon=True, name="SessionWatchdog")
        self.bus = bus
        self.execution_id = execution_id
        self.stall_seconds = stall_seconds
        self._stop_event = threading.Event()    # 勿用 _stop：覆盖 Thread._stop 方法
        self._last_activity = time.monotonic()
        self.tripped = False
        self._sub = None
        if bus is not None:
            self._sub = lambda e: self.touch()
            bus.subscribe(self._sub)

    def touch(self):
        self._last_activity = time.monotonic()

    def run(self):
        while not self._stop_event.is_set():
            time.sleep(5)
            if self.tripped:
                continue
            if time.monotonic() - self._last_activity > self.stall_seconds:
                self.tripped = True
                if self.bus is not None:
                    from runtime.events.schema import make_event
                    self.bus.publish(make_event(
                        "deadlock_detected", self.execution_id,
                        detail=f"事件静默 {self.stall_seconds}s，判定执行卡死"))

    def stop(self):
        self._stop_event.set()
        if self.bus is not None and self._sub is not None:
            try:
                self.bus.unsubscribe(self._sub)    # 审查 P1：取消订阅防泄漏
            except Exception:
                pass
        import threading
        if threading.current_thread() is not self:
            self.join(timeout=2)
