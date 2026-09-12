"""TargetRecord — 单目标生命周期记录。

attempts/结果/失败分类，会话结束可统计。
补 duration/retry 维度（统计不只 success/fail）。
"""
import time


class TargetRecord:
    """单目标生命周期记录——attempts/结果/失败分类，会话结束可统计。

    补 duration/retry 维度（统计不只 success/fail）。
    """

    def __init__(self, target_id):
        self.target_id = target_id
        self.status = "pending"
        self.attempts = 0
        self.last_error = None
        self.category = None
        self.started_at = None
        self.duration_s = None
        self.retry_count = 0

    def mark_start(self):
        # 耗时计时用 monotonic（NTP/改时间不干扰 duration）
        self.started_at = time.monotonic()

    def mark_finish(self):
        if self.started_at is not None:
            self.duration_s = round(time.monotonic() - self.started_at, 1)

    def to_dict(self):
        return {"target": self.target_id, "status": self.status,
                "attempts": self.attempts, "error": self.last_error,
                "category": self.category, "duration_s": self.duration_s,
                "retry_count": self.retry_count}
