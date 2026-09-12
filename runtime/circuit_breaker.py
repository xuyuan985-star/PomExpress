"""runtime.circuit_breaker — 向后兼容薄转发（canonical 实现已下沉 runtime.infra）。

DS 审计 V3 解环：新代码请直接 `from runtime.infra.circuit_breaker import ...`。
本模块保留旧 import 路径可用
单例对象与 infra 侧是同一实例（状态共享，不断熔断语义）。
"""
from runtime.infra.circuit_breaker import (    # noqa: F401
    CircuitBreaker,
    CircuitOpenError,
)

__all__ = ["CircuitBreaker", "CircuitOpenError"]
