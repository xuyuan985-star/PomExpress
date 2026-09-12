import random


class NaturalnessPolicy:
    """人为自然性延迟策略。

    / enabled=False → 确定性（取范围中点）；seed 固定 → 同 seed 下随机
    序列可复现（replay/回归不因 random 漂移而状态不同）。
    """

    def __init__(self, enabled=True, seed=None):
        self.enabled = enabled
        self._rng = random.Random(seed)
        # 参数范围启动即校验（非法配置提前暴露，不产生随机异常行为）
        assert enabled in (True, False), f"enabled 必须为 bool: {enabled}"
        self.click_delay_range = (200, 800)
        assert self.click_delay_range[0] >= 0, "click_delay_range 下界不能为负"
        self.transition_jitter_ms = 1000
        assert self.transition_jitter_ms >= 0, "transition_jitter_ms 不能为负"
        self.sprint_duration_variance = 0.15
        assert 0 <= self.sprint_duration_variance < 1, "sprint_duration_variance 应在 [0,1)"
        self.correction_interval_variance = 0.2
        assert 0 <= self.correction_interval_variance < 1, "correction_interval_variance 应在 [0,1)"
        self.max_continuous_rotate_s = 3
        assert self.max_continuous_rotate_s > 0, "max_continuous_rotate_s 必须为正"
        self.max_retry = {"interaction": 2, "portal": 2, "movement": 3}

    def click_delay(self):
        return self._delay(*self.click_delay_range)

    def transition_wait(self, base_seconds):
        if not self.enabled:
            return base_seconds
        return max(0.5, base_seconds + self._rng.uniform(-1, 1) * self.transition_jitter_ms / 1000.0)

    def sprint_duration(self, base_seconds):
        if not self.enabled:
            return base_seconds
        v = self._rng.uniform(-1, 1) * self.sprint_duration_variance * base_seconds
        return max(0.3, base_seconds + v)

    def rotate_duration(self):
        if not self.enabled:
            return 1.0
        return self._rng.uniform(0.5, min(self.max_continuous_rotate_s, 1.2))

    def _delay(self, lo, hi):
        if not self.enabled:
            return (lo + hi) / 2.0 / 1000.0
        return self._rng.uniform(lo, hi) / 1000.0
