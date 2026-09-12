"""ObserveOnlyInput（第 9 步：降级驱动——无权限时系统不崩溃）。

当输入不可用（UIPI/非管理员，ISSUE-09/11）时，executor 仍可运行：
所有原语返回结构化失败（observe_only），分类/恢复建议照常
"点击失败→重试→崩溃"变成"明确告知只能观察"。
实现 InputBackendProtocol 全套（executor 原语分派不炸）。
"""
from runtime.input.base import InputResult


class ObserveOnlyInput:
    """输入只读降级：任何执行原语 → observe_only 失败（retryable=False 语义）。"""

    name = "observe"

    def __init__(self):
        self.available = False

    @staticmethod
    def _blocked(action):
        return InputResult(success=False, action=action, backend="observe",
                           error="observe_only:input_unavailable")

    def click(self, x, y) -> InputResult:
        return self._blocked("click")

    def press_key(self, key, wait_time=0.2) -> InputResult:
        return self._blocked("press_key")

    def release_key(self, key) -> InputResult:
        return self._blocked("release_key")

    def click_template(self, path, threshold, max_retries, scale_range=None) -> InputResult:
        return self._blocked("click_template")

    def click_text(self, text, include, max_retries, crop) -> InputResult:
        return self._blocked("click_text")
