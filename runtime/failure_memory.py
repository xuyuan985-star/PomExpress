"""FailureMemory（Sprint E-10：失败即经验——jsonl 追加式，失败学习输入）。

Planner/策略层可查询历史失败（同目标/同原因频率），用于降低置信或换策略。
审查 P1：record 加锁（GUI + runner 并发写同一 jsonl 可能交错损坏行）。
"""
import json
import os
import threading
import time
from pathlib import Path
from config.settings import data_path

# 失败记忆文件。可用环境变量 FM_PATH 覆盖——测试应指向 logs/_pytest_tmp/，
# 避免测试的失败记录混进真实运行数据（memory/ 是运行时数据，不是测试草稿区）。
MEMORY_PATH = Path(
    os.environ.get("FM_PATH")
    or (data_path("memory") / "failures.jsonl")
)

_record_lock = threading.Lock()


class FailureMemory:
    def __init__(self, path=None):
        self.path = Path(path) if path else MEMORY_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, failure, context=None, solution=None):
        """追加一条失败经验（线程安全——单行原子写）。"""
        entry = {
            "ts": time.time(),
            "failure": failure,
            "context": context or {},
            "solution": solution,
        }
        with _record_lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
        return entry

    def query(self, failure=None, target=None, limit=20):
        """查询历史失败（按 failure 子串 / context.target 过滤）。"""
        out = []
        if not self.path.exists():
            return out
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            if failure and failure not in e.get("failure", ""):
                continue
            if target and e.get("context", {}).get("target") != target:
                continue
            out.append(e)
            if len(out) >= limit:
                break
        return out

    def count(self, failure, target=None):
        return len(self.query(failure=failure, target=target))
