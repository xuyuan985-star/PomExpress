"""BasePage：统一页面框架（Header + Content + Footer）。

所有页面继承——消除"每页自己堆布局、风格漂移"。
同时提供通用卡片工具，避免 placeholder/command_deck 重复定义。

R2（S2-B）：error_page / placeholder_page 从原 placeholder.py 迁入此处——
页面构造异常兜底与基础页骨架同源。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLayout,
                               QScrollArea, QVBoxLayout, QWidget)

from qfluentwidgets import BodyLabel, CardWidget, StrongBodyLabel

# U-36: 单一色源——所有色值/字号 token 从 theme.py 单向 import，
# 避免"两份常量漂移"（审查报告 U-06/U-36 根因）。
from gui.theme import (
    TEXT_MAIN, TEXT_MUTED, ACCENT, WARN, DANGER,
)


# Fluent 设计 token（与 theme.py 对齐；U-36 改用 import）

FONT_TITLE = 18      # 页面标题
FONT_MODULE = 14     # 卡片标题/模块标题
FONT_BODY = 13       # 正文
FONT_CAPTION = 12    # 辅助说明

# 旧名（COLOR_TEXT_MAIN/COLOR_ACCENT 等）保留为别名——外部引用
# 兼容（command_deck/placeholder 历史代码用了这些名字）。
COLOR_TEXT_MAIN = TEXT_MAIN
COLOR_TEXT_MUTED = TEXT_MUTED
COLOR_ACCENT = ACCENT
COLOR_WARN = WARN
COLOR_DANGER = DANGER


# 通用卡片工具

def card_layout(card):
    """给 CardWidget 初始化一个统一内边距的垂直布局。"""
    if card.layout() is None:
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
    return card.layout()


def card_title(card, text):
    """在卡片顶部插入一个模块级标题。"""
    label = StrongBodyLabel(text)
    label.setStyleSheet(
        f"font-size: {FONT_MODULE}px; font-weight: 700; letter-spacing: 1px;")
    card_layout(card).insertWidget(0, label)
    return label


# 页面框架

class BasePage(QWidget):
    """页面骨架：
        ┌─────────────────────────┐
        │ Header（标题 + 状态/操作）│
        ├─────────────────────────┤
        │ Content（占剩余全部） │
        ├─────────────────────────┤
        │ Footer（可选，固定高度） │
        └─────────────────────────┘
    """

    #: 子类置 True：内容超出可视高度时提供滚动，而不是把卡片压扁。
    #: 已有自带 QScrollArea 的页面保持 False（避免嵌套滚动）。
    scrollable = False

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(12)

        # Header：标题 + 右侧状态槽
        self._header = QWidget()
        h = QHBoxLayout(self._header)
        h.setContentsMargins(4, 0, 4, 0)
        self.title_label = StrongBodyLabel(title)
        self.title_label.setStyleSheet(
            f"font-size: {FONT_TITLE}px; font-weight: 700;")
        h.addWidget(self.title_label)
        h.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: {FONT_CAPTION}px;")
        h.addWidget(self.status_label)
        self._layout.addWidget(self._header, 0)

        # Content 容器（占剩余全部——无 stretch 空洞）
        #
        # scrollable=True 时把内容包进 QScrollArea：内容高于可视区时**滚动**，
        # 而不是把每张卡片压扁到看不清（Qt 在无滚动容器里只能压缩子控件，
        # 表现为「所有设置项挤在一起」）。
        # **默认关闭**：自带 QScrollArea 的页面（command_deck / guides_view）
        # 若再包一层会形成嵌套滚动，故由子类按需开启。
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)

        if self.scrollable:
            # 关键：让内容布局的最小高度 = 其自然高度（SetMinimumSize）。
            # 否则 QScrollArea 会把控件压到 minimumSizeHint 才滚动——
            # 表现为「能滚，但卡片仍被压扁了一点」，仍不好看。
            self.content_layout.setSizeConstraint(
                QLayout.SizeConstraint.SetMinimumSize)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)   # 无边框（与页面风格一致）
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(self.content)
            self._scroll = scroll
            self._layout.addWidget(scroll, 1)
        else:
            self._scroll = None
            self._layout.addWidget(self.content, 1)

        # Footer（默认隐藏）
        self.footer = QWidget()
        self.footer.setVisible(False)
        self._layout.addWidget(self.footer, 0)

    # 子类接口

    def set_embedded(self):
        """嵌入模式：隐藏自身页头/页脚（供页面嵌套复用，防重复标题）。"""
        self._header.setVisible(False)
        self.footer.setVisible(False)
        return self

    def set_status(self, text, busy=False):
        self.status_label.setText(text)
        color = COLOR_ACCENT if busy else COLOR_TEXT_MUTED
        self.status_label.setStyleSheet(
            f"color: {color}; font-size: {FONT_CAPTION}px;")

    def add_footer(self, widget):
        f = self.footer.layout()
        if f is None:
            f = QHBoxLayout(self.footer)
            f.setContentsMargins(0, 0, 0, 0)
            f.setSpacing(8)
        f.addWidget(widget)
        self.footer.setVisible(True)

    def card(self):
        """通用卡片容器（Fluent 分层）。"""
        c = CardWidget()
        card_layout(c)
        return c


# 错误/占位页（R2：从 placeholder.py 迁入；行为保持）


def error_page(source, error):
    """页面构造失败兜底页（显示错误而非空白/崩溃）。
    U-28: 与 placeholder_page 不同——错误页语义是「故障」不是「未开发」，
    用「加载失败」徽标（DANGER 红色）而非「功能开发中」（WARN 琥珀未实现）。"""
    return placeholder_page(
        f"{source} 初始化失败",
        f"错误: {error}",
        badge_text="加载失败",
        badge_color=DANGER,
    )


def placeholder_page(title, note, badge_text="功能开发中",
                     badge_color=None):
    """占位页：卡片置顶（无顶部 stretch 空洞）。
    明确标注「功能开发中」——防止用户误以为功能已实现。
    U-28: 参数化 badge——error_page 复用同一壳但换语义与色（红色 = 故障）。
    """
    page = BasePage(title)
    card = CardWidget()
    card_layout(card)
    badge = BodyLabel(badge_text)
    if badge_color is None:
        badge_color = WARN  # 默认「未开发」= WARN 琥珀
    badge.setStyleSheet(
        f"color: {badge_color}; font-weight: 700; font-size: 13px;")
    card_layout(card).addWidget(badge)
    label = BodyLabel(note)
    label.setStyleSheet(f"color: {TEXT_MUTED};")
    card_layout(card).addWidget(label)
    page.content_layout.addWidget(card)
    page.content_layout.addStretch(1)
    return page
