"""runtime.metrics — 向后兼容薄转发（canonical 实现已下沉 runtime.infra）。

DS 审计 V3 解环：新代码请直接 `from runtime.infra.metrics import ...`。
本模块保留旧 import/mock 路径（runtime.metrics.METRICS 等）可用
单例对象与 infra 侧是同一实例（指标状态共享）。
"""
from runtime.infra.metrics import MetricsCollector, METRICS    # noqa: F401

__all__ = ["MetricsCollector", "METRICS"]
