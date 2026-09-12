"""Emergency Pause：人工安全刹车（v0.12.1，M1 阶段强制开启）。

任何"人接手了电脑"的信号 → 立即 human_intervention + pause，绝不继续执行。

权限边界（Errata）：本层只回答"还能不能安全继续"，
**不监控**：键盘输入 / 行为分析 / 用户意图识别（禁止演化为输入监控系统）。
触发源仅限：光标移动、前台窗口切换。
"""
import ctypes
import threading
import time
from ctypes import wintypes

# 可调 · Windows 虚拟键码（Esc 安全停止热键）
# VK_ESCAPE: Windows VK code for Esc（0x1B，协议常量，勿改）。
# 用途：`EmergencyMonitor.run()` 轮询 GetAsyncKeyState，用户按 Esc 时触发
# 「手动热键」中断——游戏卡死、UI 异常时唯一的强制停止入口。
VK_ESCAPE = 0x1B


class EmergencyMonitor(threading.Thread):
    def __init__(self, bus, execution_id, game_hwnd, poll_interval=0.5, cursor_radius=30):
        super().__init__(daemon=True)
        self.bus = bus
        self.execution_id = execution_id
        self.game_hwnd = game_hwnd
        self.poll_interval = poll_interval
        self.cursor_radius = cursor_radius
        self._stop = threading.Event()
        self._last_cursor = None
        self._paused = False
        self._mouse_check_enabled = True
        self.user32 = ctypes.windll.user32
        self._last_esc = False    # Esc 安全停止热键（防抖动）
        self._esc_pressed_at = 0.0

    def _get_cursor(self):
        pt = wintypes.POINT()
        self.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def _emit(self, event_type, **kw):
        if self.bus is None:
            return
        from runtime.events.schema import make_event
        self.bus.publish(make_event(event_type, self.execution_id, **kw))

    def run(self):
        self._last_cursor = self._get_cursor()
        while not self._stop.is_set():
            time.sleep(self.poll_interval)
            if self._paused:
                continue
            # Esc 安全停止热键（轮询——游戏卡死时仍可停止）
            down = bool(self.user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
            now = time.time()
            if down and not self._last_esc and now - self._esc_pressed_at > 2.0:
                self._esc_pressed_at = now
                self._trigger("esc_hotkey", "Esc 安全停止（手动热键）")
                continue
            self._last_esc = down
            cx, cy = self._get_cursor()
            lx, ly = self._last_cursor
            if self._mouse_check_enabled and (abs(cx - lx) > self.cursor_radius or abs(cy - ly) > self.cursor_radius):
                self._trigger("cursor_moved", f"光标移动 ({lx},{ly})→({cx},{cy})")
                continue
            fg = self.user32.GetForegroundWindow()
            if self.game_hwnd and fg != self.game_hwnd:
                self._trigger("window_switch", f"前台窗口已切换 (0x{fg:x} != 0x{self.game_hwnd:x})")

    def suspend_mouse(self):
        """机器人自身点击（SetCursorPos/SendInput）期间挂起光标检测——防自伤。"""
        self._mouse_check_enabled = False

    def resume_mouse(self):
        """操作结束：恢复检测并以当前光标为新基准（点击后光标停在目标处）。"""
        self._mouse_check_enabled = True
        self._last_cursor = self._get_cursor()

    def _trigger(self, reason, detail):
        self._paused = True
        self._emit("human_intervention", context={"reason": reason, "detail": detail})
        self._emit("pause_requested", context={"reason": "human_intervention",
                                               "detail": f"{reason}: {detail}"})

    def is_paused(self):
        return self._paused

    def resume(self):
        self._paused = False
        self._last_cursor = self._get_cursor()

    def stop(self):
        self._stop.set()
