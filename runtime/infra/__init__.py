"""runtime.infra — 与业务无关的基础设施（熔断/指标）。

DS 审计 V3 解环：circuit_breaker/metrics 是纯基础设施——ingest
离线管线依赖它们是合法的"下层依赖"，而 runtime 业务模块依赖它们也是
"下层依赖"。双向环的根因是它们寄生在 runtime 顶层包（ingest→runtime
的每条边都经过它们）。下沉到 runtime.infra 后：

- 合法边：runtime.* → runtime.infra.*、ingest.* → runtime.infra.*
- 非法边（门禁拦截）：runtime.* → ingest.*、ingest.* → runtime.<非infra>

向后兼容：runtime.circuit_breaker / runtime.metrics 保留为薄转发，
旧 import 路径（runtime.circuit_breaker.* 等）
runtime.metrics.METRICS）继续可用。
"""
