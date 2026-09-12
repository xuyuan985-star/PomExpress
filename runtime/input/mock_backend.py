from runtime.input.base import InputBackend, InputResult


class MockBackend(InputBackend):
    """模拟后端（dry_run 使用）：不产生真实输入，全部成功。

    修复（0.6.0 ）：补齐 `key_down` / `key_up`——原代码未实现
    （走 `InputBackend` 基类默认返回 `success=False`），导致
    `replayer._replay_key` 看到失败就跳过按键，dry_run 报告成功但
    实际一个键都没按（" "——掩盖真实按键逻辑的一切缺陷）。
    同时补 `calls` 调用序列记录（供测试断言 dry_run 真的按了键）。
    """

    name = "mock"

    def __init__(self):
        # 修复：调用序列记录——dry_run 检测。
        # 每个方法 append 一条 {"action": ..., **detail}，测试侧可
        # 断言 backend.calls 非空 / 含具体 key_down 调用。
        self.calls = []

    def _record(self, action, **detail):
        """记录一次调用（ 修复：dry_run 检测基础设施）。"""
        self.calls.append({"action": action, **detail})

    def click(self, x, y):
        self._record("click", x=x, y=y)
        return InputResult(success=True, action="click", backend=self.name, detail={"x": x, "y": y})

    def move(self, x, y):
        self._record("move", x=x, y=y)
        return InputResult(success=True, action="move", backend=self.name, detail={"x": x, "y": y})

    def relative_move(self, dx, dy):
        """A- dry_run 录而不发（不污染 OS）——和 move 一样
        返回 success=True + detail，便于 replayer 测试断言视角位移被记录。
        """
        self._record("relative_move", dx=dx, dy=dy)
        return InputResult(success=True, action="relative_move", backend=self.name,
                           detail={"dx": dx, "dy": dy})

    def press_key(self, key, wait_time=0.2):
        self._record("press_key", key=key, wait_time=wait_time)
        return InputResult(success=True, action="press_key", backend=self.name, detail={"key": key})

    def release_key(self, key):
        self._record("release_key", key=key)
        return InputResult(success=True, action="release_key", backend=self.name,
                           detail={"key": key})

    def key_down(self, key):
        """修复：补齐 key_down——replayer._replay_key 走此通道。

        原代码未实现，走 InputBackend 基类默认返回 success=False，
        导致 replayer 静默跳过所有按键（dry_run ）。
        """
        self._record("key_down", key=key)
        return InputResult(success=True, action="key_down", backend=self.name,
                           detail={"key": key})

    def key_up(self, key):
        """修复：与 key_down 对偶。"""
        self._record("key_up", key=key)
        return InputResult(success=True, action="key_up", backend=self.name,
                           detail={"key": key})

    def execute(self, intent):
        self._record("execute", action=intent.action,
                     target=intent.target, method=intent.method)
        return InputResult(success=True, action=intent.action, backend=self.name,
                           detail={"target": intent.target, "method": intent.method})
