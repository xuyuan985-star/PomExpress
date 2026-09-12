from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from qfluentwidgets import Theme, qconfig, setThemeColor

BG_DEEP = "#0B1524"
BG_PANEL = "#0F1B2D"
BG_CARD = "#16283F"
BG_CARD_HOVER = "#1B3050"
BORDER = "#24405F"
ACCENT = "#4FD1C5"
ACCENT_DIM = "#2A7D78"
TEXT_MAIN = "#E6F1FF"
TEXT_MUTED = "#7A90B0"
WARN = "#FFB454"
DANGER = "#FF6B6B"
OK = "#4FD1C5"

# Selection highlight matches ACCENT so native controls don't flash default blue.
SELECTION_BG = ACCENT
SELECTION_FG = BG_DEEP

GLOBAL_QSS = f"""
QWidget {{
    font-family: "Microsoft YaHei UI";
    color: {TEXT_MAIN};
    /* N-2（u6 终审收尾）：移除全局 background-color——之前的 QWidget
       规则会压平 qfluentwidgets CardWidget 自绘背景（卡片与页面同色，
       失去 ~2% 半透明提亮的视觉层次）。背景职责下放给具名容器：
       QMainWindow/QDialog/#stackedWidget/#titleBar/#navigationInterface。
       CardWidget/LineEdit/ComboBox/... 等具体控件由下文专属规则
       提供 background-color，无专属规则则继承父容器。 */
}}
QMainWindow,
QDialog {{
    background-color: {BG_PANEL};
}}
#titleBar {{
    background: {BG_PANEL};
    border-bottom: 1px solid {BORDER};
}}
#navigationInterface {{
    background: {BG_DEEP};
    border-right: 1px solid {BORDER};
}}
#stackedWidget {{
    background: {BG_PANEL};
}}
#brandLabel {{
    color: {TEXT_MAIN};
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 2px;
}}
#brandSubLabel {{
    color: {TEXT_MUTED};
    font-size: 10px;
    letter-spacing: 1px;
}}

/* --- U-01: native control QSS (covers Qt widgets that ignore QPalette) --- */
QPushButton,
QToolButton {{
    background-color: {BG_CARD};
    color: {TEXT_MAIN};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 12px;
}}
QPushButton:hover,
QToolButton:hover {{
    background-color: {BG_CARD_HOVER};
    border-color: {ACCENT_DIM};
}}
QPushButton:pressed,
QToolButton:pressed {{
    background-color: {BG_DEEP};
}}
QPushButton:disabled,
QToolButton:disabled {{
    background-color: {BG_DEEP};
    color: {TEXT_MUTED};
    border-color: {BORDER};
}}
QPushButton:default {{
    border-color: {ACCENT};
}}

QLineEdit,
QSpinBox,
QDoubleSpinBox,
QPlainTextEdit,
QTextEdit,
QAbstractSpinBox {{
    background-color: {BG_CARD};
    color: {TEXT_MAIN};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 3px 6px;
    selection-background-color: {SELECTION_BG};
    selection-color: {SELECTION_FG};
}}
QLineEdit:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QPlainTextEdit:focus,
QTextEdit:focus {{
    border-color: {ACCENT};
}}
QLineEdit:disabled,
QSpinBox:disabled,
QPlainTextEdit:disabled,
QTextEdit:disabled {{
    color: {TEXT_MUTED};
    background-color: {BG_DEEP};
}}

QComboBox,
QComboBox:editable {{
    background-color: {BG_CARD};
    color: {TEXT_MAIN};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 3px 8px;
    selection-background-color: {SELECTION_BG};
    selection-color: {SELECTION_FG};
}}
QComboBox:hover {{
    border-color: {ACCENT_DIM};
}}
QComboBox:disabled {{
    color: {TEXT_MUTED};
    background-color: {BG_DEEP};
}}
QComboBox QAbstractItemView {{
    background-color: {BG_CARD};
    color: {TEXT_MAIN};
    border: 1px solid {BORDER};
    selection-background-color: {SELECTION_BG};
    selection-color: {SELECTION_FG};
    outline: 0;
}}

QCheckBox,
QRadioButton {{
    background: transparent;
    color: {TEXT_MAIN};
    spacing: 6px;
}}
QCheckBox::indicator,
QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    background-color: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 2px;
}}
QRadioButton::indicator {{
    border-radius: 7px;
}}
QCheckBox::indicator:hover,
QRadioButton::indicator:hover {{
    border-color: {ACCENT_DIM};
}}
QCheckBox::indicator:checked,
QRadioButton::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}
QCheckBox:disabled,
QRadioButton:disabled {{
    color: {TEXT_MUTED};
}}

QListView,
QListWidget,
QTreeView,
QTreeWidget,
QTableView,
QTableWidget,
QHeaderView {{
    background-color: {BG_CARD};
    color: {TEXT_MAIN};
    border: 1px solid {BORDER};
    alternate-background-color: {BG_PANEL};
    selection-background-color: {SELECTION_BG};
    selection-color: {SELECTION_FG};
    outline: 0;
}}
QListView::item:hover,
QListWidget::item:hover,
QTreeView::item:hover,
QTreeWidget::item:hover {{
    background-color: {BG_CARD_HOVER};
}}
QListView::item:selected,
QListWidget::item:selected,
QTreeView::item:selected,
QTreeWidget::item:selected {{
    background-color: {SELECTION_BG};
    color: {SELECTION_FG};
}}
QHeaderView::section {{
    background-color: {BG_DEEP};
    color: {TEXT_MUTED};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 4px 8px;
}}
QTreeWidget::branch {{
    background: transparent;
}}

QScrollBar:vertical {{
    background: {BG_PANEL};
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {ACCENT_DIM};
}}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
    background: none;
}}
QScrollBar:horizontal {{
    background: {BG_PANEL};
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {ACCENT_DIM};
}}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0;
    background: none;
}}

QGroupBox {{
    background-color: {BG_PANEL};
    color: {TEXT_MAIN};
    border: 1px solid {BORDER};
    border-radius: 4px;
    margin-top: 12px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    color: {TEXT_MUTED};
}}

QToolTip {{
    background-color: {BG_DEEP};
    color: {TEXT_MAIN};
    border: 1px solid {BORDER};
}}

QMenu {{
    background-color: {BG_CARD};
    color: {TEXT_MAIN};
    border: 1px solid {BORDER};
}}
QMenu::item:selected {{
    background-color: {SELECTION_BG};
    color: {SELECTION_FG};
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 4px 6px;
}}

QMessageBox {{
    background-color: {BG_PANEL};
}}
QMessageBox QLabel {{
    color: {TEXT_MAIN};
}}
"""


def _build_dark_palette():
    """Return a QPalette consistent with our dark token set.

    U-01 fix: without this, every QWidget family (QPushButton/QListWidget/etc.)
    keeps the system light palette, and the global QSS color override renders
    light text on a light background. Offscreen probe before fix:
        palette Base = #ffffff, Text = #e6f1ff (invisible).
    """
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG_PANEL))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_MAIN))
    pal.setColor(QPalette.ColorRole.Base, QColor(BG_CARD))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(BG_PANEL))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT_MAIN))
    pal.setColor(QPalette.ColorRole.Button, QColor(BG_CARD))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT_MAIN))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(SELECTION_BG))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(SELECTION_FG))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(BG_DEEP))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT_MAIN))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_MUTED))
    pal.setColor(QPalette.ColorRole.BrightText, QColor(DANGER))
    pal.setColor(QPalette.ColorRole.Link, QColor(ACCENT))
    # Disabled variants — keep readable but visually muted.
    disabled = pal  # alias
    disabled.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(TEXT_MUTED))
    disabled.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(TEXT_MUTED))
    disabled.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(TEXT_MUTED))
    disabled.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor(BG_DEEP))
    disabled.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor(BG_DEEP))
    return pal


def apply_theme(app):
    qconfig.theme = Theme.DARK
    setThemeColor(QColor(ACCENT))
    # U-01: install a project-wide dark palette BEFORE the QSS. Native Qt
    # controls (QTreeView/QListView/QLineEdit/QComboBox/QPushButton/...) that
    # ignore QPalette still fall back to the palette for colors the QSS does
    # not override, so this single set covers most "white-on-white" cases.
    app.setPalette(_build_dark_palette())
    app.setStyleSheet(GLOBAL_QSS)
    # Hint to Qt: enable high-DPI pixmap smoothing for crisp tree/list icons.
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)
