"""GameHudOverlay：游戏窗口左下角日志 HUD（对齐 M7 行为）。

无边框置顶半透明层，跟随游戏窗口位置（左下角）
顶部固定提示"F10 = 紧急停止"
内容 = EventBus 事件流（滚动日志）
"""
from collections import deque

from PySide6.QtCore import QObject, QPoint, Qt
from PySide6.QtWidgets import (QLabel, QPlainTextEdit,
                               QVBoxLayout, QWidget)


class GameHudOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # 对齐 M7 overlay：topmost + 点击穿透（不挡游戏操作）+ 不抢焦点
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint
                            | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(420, 200)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 顶部提示条
        tip = QLabel("F10 = 紧急停止")
        tip.setStyleSheet(
            "background: rgba(230,69,69,200); color: white;"
            "font-size: 12px; font-weight: 700; padding: 3px 8px;"
            "border-top-left-radius: 6px; border-top-right-radius: 6px;")
        lay.addWidget(tip)

        # 日志区（隐藏滚动条——只滚最新几行，滚动条在悬浮层无意义）
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(100)
        self.log_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.log_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.log_view.setStyleSheet(
            "background: rgba(16,24,38,200); color: #B0C4DE;"
            "font-size: 11px; border: none;"
            "border-bottom-left-radius: 6px; border-bottom-right-radius: 6px;")
        lay.addWidget(self.log_view)

    def append(self, text):
        self.log_view.appendPlainText(text)
        # 自动滚到最新
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_emergency(self):
        """紧急停止时提示（红色横幅）。"""
        self.log_view.appendPlainText("⚠ 已紧急停止（F10）——全部按键已释放")
        # 不强制 show——HUD 已收起（任务结束）时
        # F10 不应把它重新弹出（main_window._emergency_hotkey 调用点）
        if not self.isVisible():
            self.hide()


class GameHudController(QObject):
    """HUD 生命周期：跟随游戏窗口（位置/可见性），绑定事件流。

    审查根因（多轮实测）：本环境 QThread/Qt 跨线程信号 → QObject 槽不投递
    （dict/str 均失效）——日志入 _lines 队列（跨线程 append 原子），主线程
    QTimer 轮询消费（与 HealthWorker/TaskProcess 同款轮询模式）。
    """

    def __init__(self, bus, game_hwnd, log_lines=100, parent=None):
        super().__init__(parent)
        self.bus = bus
        self.game_hwnd = game_hwnd
        self.log_lines = log_lines
        self.overlay = GameHudOverlay()
        # deque(maxlen=N)：append 自动丢弃头部——消除 list 截断赋值
        # self._lines = self._lines[-N:] 创建新 list 引用与 _flush 的
        # while-pop 循环之间的逻辑竞态（T9-B-01 修复）。
        self._lines = deque(maxlen=self.log_lines)
        self.bus.subscribe(self._on_event)
        # 主线程轮询消费（跨线程信号不可靠——轮询兜底）
        from PySide6.QtCore import QTimer
        self._poll = QTimer(self)
        self._poll.setInterval(200)
        self._poll.timeout.connect(self._flush)
        self._poll.start()

    def _on_event(self, event):
        # 发布线程（Runner）回调——只入队，不碰 Qt 控件
        line = f"[{event.type}] {event.detail or ''}".strip()
        self._enqueue(line)

    def append_external(self, line):
        """外部日志入口（m7 任务子进程 stdout——不经 EventBus）。

        与 _on_event 同语义：任意线程可调，只入队（主线程轮询消费）。
        """
        self._enqueue(line)

    def _enqueue(self, line):
        """入队（deque.append 原子 + maxlen 自动截断——T9-B-01 修复）。"""
        if not line:
            return
        if len(line) > 90:
            line = line[:90] + "…"
        self._lines.append(line)

    def _flush(self):
        """主线程轮询消费队列 → overlay（隐藏时丢弃——恢复后只显历史）。"""
        if not self.overlay.isVisible():
            return
        while self._lines:
            self.overlay.append(self._lines.popleft())

    def show(self):
        self.overlay.show()
        for line in list(self._lines)[-20:]:
            self.overlay.log_view.appendPlainText(line)
        # 补历史后清队列：避免 _flush 重复消费（潜伏 bug：复用 show 场景下
        # _flush 再 pop 会把刚补的历史再显示一遍）
        self._lines.clear()
        sb = self.overlay.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())
        self.reposition()

    def reposition(self):
        """跟随游戏窗口左下角。"""
        import ctypes
        import ctypes.wintypes
        hwnd = self.game_hwnd
        if not ctypes.windll.user32.IsWindow(hwnd):
            return
        rect = ctypes.wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        w = self.overlay.width()
        h = self.overlay.height()
        # 左下角（窗口底部上方 8px）
        x = rect.left + 8
        y = rect.bottom - h - 8
        self.overlay.move(QPoint(x, y))

    def hide(self):
        self.overlay.hide()

    def destroy(self):
        # T9-B-02 修复：先停 timer 再销毁——防止 deleteLater 后 timer 仍触发
        # _flush 访问已标记删除的 overlay（Qt 中"访问已释放对象"是异常崩溃主因）。
        self._poll.stop()
        self.bus.unsubscribe(self._on_event)
        self.overlay.deleteLater()
