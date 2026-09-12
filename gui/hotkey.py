"""全局热键（对齐 M7：用 keyboard 库——RegisterHotKey/nativeEvent 在此环境不可靠）。

keyboard.on_press_key 后台线程回调 → 只入队（线程安全）→ QTimer 主线程
轮询 emit pressed——绕开"本环境跨线程 Qt 信号不投递"的坑（信号只在主线程
emit，保证到达槽）。
"""
import threading
from collections import deque

from PySide6.QtCore import QObject, QTimer, Signal


class GlobalHotkey(QObject):
    pressed = Signal(str)

    def __init__(self, parent=None, poll_interval=80):
        super().__init__(parent)
        self._handlers = {}
        # deque：append/popleft 原子——审查：list 整体替换在 keyboard 线程
        # 入队与主线程交换引用之间竞态会丢按键（F10 紧急停止丢失）
        self._pending = deque()
        self._lock = threading.Lock()
        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def register(self, key, callback=None):
        """注册全局热键：key='f10'；回调在主线程（经 pressed 信号分发）。

        callback 参数保留兼容（实际经 pressed 信号分发——审查 P1 死参数
        保留接口但明确无行为）。keyboard 回调线程只入队，不 emit。
        """
        import keyboard
        with self._lock:
            if key in self._handlers:
                return
            handler = keyboard.on_press_key(
                key, lambda e: self._push(key), suppress=False)
            self._handlers[key] = handler
            # T9-B-03 配套：unregister_all 会停 timer——重新注册时重启。
            if not self._timer.isActive():
                self._timer.start()

    def _push(self, key):
        """keyboard 后台线程调用——只入队（deque append 原子）。"""
        self._pending.append(key)

    def _poll(self):
        """主线程 QTimer 轮询：取走队列 → 主线程内 emit（可靠投递）。"""
        keys = []
        try:
            while True:
                keys.append(self._pending.popleft())
        except IndexError:
            pass
        for k in keys:
            self.pressed.emit(k)

    def unregister_all(self):
        import keyboard
        with self._lock:
            for k, h in list(self._handlers.items()):
                try:
                    keyboard.unhook(h)
                except Exception:
                    pass
            self._handlers.clear()
            self._pending.clear()
            # T9-B-03 修复：注销时停掉 80ms 轮询 timer——防止注销后空跑。
            # register() 会重启（停/启成对，不破坏后续注册）。
            self._timer.stop()
