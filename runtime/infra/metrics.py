"""统一监控指标（MetricsCollector）——成功率/失败率/耗时/延迟。

进程级单例：任务、动作、视觉三个维度，可导出 JSON 快照。

DS 审计 V3 解环：本模块是 canonical 实现——原 runtime/metrics.py
已降级为薄转发（向后兼容旧 import/mock 路径）。新代码请直接
`from runtime.infra.metrics import ...`。
"""
import threading
import time


class MetricsCollector:
    def __init__(self):
        self._lock = threading.Lock()
        self.tasks = {"total": 0, "done": 0, "failed": 0}
        self.actions = {"total": 0, "success": 0, "failed": 0,
                        "total_ms": 0.0}
        self.vision = {"template_matches": 0, "template_miss": 0,
                       "total_ms": 0.0}
        # 模型调用指标（token 成本/延迟/错误分类）
        self.models = {"calls": 0, "total_ms": 0.0,
                       "prompt_tokens": 0, "completion_tokens": 0,
                       "errors": {"timeout": 0, "rate_limit": 0, "auth": 0,
                                  "invalid_json": 0, "other": 0}}
        self.started_at = time.time()
        # 采样周期 + 历史趋势（timeseries 保留最近 N 点）
        self.sample_interval = 30.0
        self._timeseries = []    # [{t, cpu, mem, actions_rate}...]
        self._ts_max = 120    # 分钟（30s 间隔）
        self._last_sample = 0.0

    def task_done(self, success):
        with self._lock:
            self.tasks["total"] += 1
            self.tasks["done" if success else "failed"] += 1

    def action(self, success, ms):
        with self._lock:
            self.actions["total"] += 1
            self.actions["success" if success else "failed"] += 1
            self.actions["total_ms"] += ms

    def template_match(self, hit, ms):
        with self._lock:
            if hit:
                self.vision["template_matches"] += 1
            else:
                self.vision["template_miss"] += 1
            self.vision["total_ms"] += ms

    def model_call(self, ms, prompt_tokens=0, completion_tokens=0, error=None):
        """模型调用统计（耗时/token/错误分类）。"""
        with self._lock:
            self.models["calls"] += 1
            self.models["total_ms"] += ms
            self.models["prompt_tokens"] += prompt_tokens or 0
            self.models["completion_tokens"] += completion_tokens or 0
            if error:
                cls = "other"
                if "timeout" in error.lower() or "timed out" in error.lower():
                    cls = "timeout"
                elif "quota" in error.lower() or "429" in error:
                    cls = "rate_limit"
                elif "401" in error or "auth" in error.lower():
                    cls = "auth"
                elif "json" in error.lower() or "parse" in error.lower():
                    cls = "invalid_json"
                self.models["errors"][cls] += 1

    def sample(self):
        """按采样周期记录性能快照（趋势分析用，防高频采样影响性能）。"""
        if time.time() - self._last_sample < self.sample_interval:
            return
        self._last_sample = time.time()
        snap = self.snapshot()
        with self._lock:
            self._timeseries.append({
                "t": snap["uptime_s"],
                "actions_total": snap["actions"]["total"],
                "actions_rate": round(
                    snap["actions"]["total"] / max(1, snap["uptime_s"]), 2),
                "models_calls": snap["models"]["calls"],
            })
            self._timeseries = self._timeseries[-self._ts_max:]

    def trend(self):
        """性能历史趋势（最近 N 个采样点）。"""
        with self._lock:
            return list(self._timeseries)

    def snapshot(self):
        with self._lock:
            a = dict(self.actions)
            t = dict(self.tasks)
            v = dict(self.vision)
            m = dict(self.models)
            m["errors"] = dict(m["errors"])
            m["avg_ms"] = round(m["total_ms"] / m["calls"], 1) if m["calls"] else None
            uptime = time.time() - self.started_at
        return {
            "uptime_s": round(uptime, 1),
            "tasks": {**t, "success_rate": round(
                t["done"] / t["total"], 3) if t["total"] else None},
            "actions": {**a, "success_rate": round(
                a["success"] / a["total"], 3) if a["total"] else None,
                "avg_ms": round(a["total_ms"] / a["total"], 1)
                if a["total"] else None},
            "vision": {**v, "avg_ms": round(v["total_ms"] / (
                v["template_matches"] + v["template_miss"]), 1)
                if (v["template_matches"] + v["template_miss"]) else None},
            "models": m,
        }


METRICS = MetricsCollector()
