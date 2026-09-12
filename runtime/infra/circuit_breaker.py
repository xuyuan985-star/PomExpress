"""熔断器（CircuitBreaker）——连续失败熔断，半开恢复探测。

适用：外部依赖调用链——连续失败不持续打，冷却后探针恢复。

DS 审计 V3 解环：本模块是 canonical 实现——原 runtime/circuit_breaker.py
已降级为薄转发（向后兼容旧 import/mock 路径）。新代码请直接
`from runtime.infra.circuit_breaker import ...`。
"""
import threading
import time


class CircuitBreaker:
    """三态：CLOSED（正常）→ OPEN（熔断）→ HALF_OPEN（探针）。"""

    def __init__(self, failure_threshold=3, cooldown_s=30, name="cb"):
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self.name = name
        self._lock = threading.Lock()
        self._failures = 0
        self._open_until = 0.0
        self._half_open = False
        self.stats = {"tripped": 0, "recovered": 0}

    @property
    def state(self):
        with self._lock:
            if self._half_open:
                return "HALF_OPEN"
            if time.time() < self._open_until:
                return "OPEN"
            return "CLOSED"

    def allow(self):
        """是否允许调用。OPEN → False（快速失败）；HALF_OPEN 放行探针。"""
        with self._lock:
            if self._half_open:
                return True    # 探针请求放行
            if time.time() < self._open_until:
                return False
            return True

    def record_success(self):
        with self._lock:
            was_half = self._half_open
            self._failures = 0
            if was_half:
                self.stats["recovered"] += 1
            self._half_open = False
            self._open_until = 0.0

    def record_failure(self):
        with self._lock:
            self._failures += 1
            if self._half_open:
                # 探针失败 → 重新熔断
                self._half_open = False
                self._open_until = time.time() + self.cooldown_s
                self.stats["tripped"] += 1
                return
            if self._failures >= self.failure_threshold:
                self._open_until = time.time() + self.cooldown_s
                self.stats["tripped"] += 1

    def _tick_half_open(self):
        """熔断冷却结束 → 半开（由调用方在 allow 时推进）。"""
        with self._lock:
            if not self._half_open and time.time() >= self._open_until \
                    and self._open_until > 0:
                self._half_open = True

    def __call__(self, fn, *args, **kwargs):
        """装饰器式调用：熔断时抛 CircuitOpenError。"""
        self._tick_half_open()
        if not self.allow():
            raise CircuitOpenError(f"{self.name} 熔断中")
        try:
            result = fn(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise


class CircuitOpenError(Exception):
    pass


