from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class InputResult:
    success: bool
    action: str
    backend: str
    error: str = None
    method: str = None     # backend 执行方法：template/text/key——action 保持 intent 语义
    detail: dict = field(default_factory=dict)

    def to_context(self, **extra):
        ctx = {"backend": self.backend, "success": self.success}
        if self.error:
            ctx["error"] = self.error
        if self.method:
            ctx["method"] = self.method
        ctx.update(self.detail)
        ctx.update(extra)
        return ctx


@runtime_checkable
class InputBackendProtocol(Protocol):
    """-3.7：输入后端契约——Fake 与真实实现都必须满足，防接口漂移。

    Fake 测试 PASS 而真机失败的主因之一 = 假实现签名漂移；测试侧
    断言 isinstance(fake, InputBackendProtocol) 即可防漂移。
    """
    name: str

    def click(self, x, y) -> InputResult: ...

    def press_key(self, key, wait_time=0.2) -> InputResult: ...

    def release_key(self, key) -> InputResult: ...

    def key_down(self, key) -> InputResult: ...

    def key_up(self, key) -> InputResult: ...

    def relative_move(self, dx, dy) -> InputResult: ...

    def click_template(self, path, threshold, max_retries, scale_range=None) -> InputResult: ...

    def click_text(self, text, include, max_retries, crop) -> InputResult: ...


class InputBackend:
    """审查 P1：基类补齐协议全部方法——此前缺 click_template/click_text，
    导致 Win32Backend/MockBackend 不满足 InputBackendProtocol（isinstance=False），
    set_input_backend 会拒绝注入真实后端。"""

    name = "base"

    def click(self, x, y) -> InputResult:
        raise NotImplementedError

    def move(self, x, y) -> InputResult:
        raise NotImplementedError

    def press_key(self, key, wait_time=0.2) -> InputResult:
        raise NotImplementedError

    def release_key(self, key) -> InputResult:
        """紧急释放（keyDown→keyUp 异常后兜底，防卡键）。"""
        raise NotImplementedError

    def key_down(self, key) -> InputResult:
        """A-W1 修复：拆出独立 key_down API（与 key_up 对偶）
        replayer chord / 长按修饰键路径走本方法。任何后端未实现时
        返回 success=False + 显式 error，避免 AttributeError 静默崩溃。
        """
        return InputResult(success=False, action="key_down", backend=self.name,
                           error=f"key_down not implemented on {self.name}")

    def key_up(self, key) -> InputResult:
        """A-W1 修复：与 key_down 对偶。"""
        return InputResult(success=False, action="key_up", backend=self.name,
                           error=f"key_up not implemented on {self.name}")

    def relative_move(self, dx, dy) -> InputResult:
        """A- 相对鼠标位移（视角移动用）——抽象给 backend，
        避免 replayer 直接 ctypes.windll.user32.mouse_event 绕过 backend
        协议（CLAUDE.md 架构：输入链 SendInputBackend → Win32Backend）。
        MockBackend 录而不发（dry_run 不污染 OS）；Win32Backend 转
        mouse_event(MOUSEEVENTF_MOVE)。
        """
        return InputResult(success=False, action="relative_move", backend=self.name,
                           error=f"relative_move not implemented on {self.name}")

    def click_template(self, path, threshold, max_retries,
                       scale_range=None) -> InputResult:
        raise NotImplementedError

    def click_text(self, text, include, max_retries, crop) -> InputResult:
        raise NotImplementedError
