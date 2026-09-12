"""CommandDeck（P1「出击」）：BasePage 框架 + 指挥台业务。

三页重做（P1）：本页 = 全 app 唯一执行页。
吸收 gui/pages/observation.py：实时画面（只留一份 3s 刷新，走 save_live_frame
  + background-image QSS）与统一时间线（事件类型计数 + 失败检查器 + 事件流，
  上限 50 条）。
世界图（世界图 / P2 藏宝图）的录制按钮迁入本页：runtime.input.recorder
  .TrajectoryRecorder，F10 转调 stop_recording()。

既有契约（main_window 依赖，保持不变）：
led_style / STATUS_TEXT / STATUS_COLOR / category_text / _status_color /
HEALTH_LABELS；CommandDeck.run_requested / stop_requested；
__init__(targets, parent=None) / on_event / set_health / set_health_status /
set_starting / set_stopping / set_run_status / reset / refresh_targets /
find_region_index。
"""
import json
import logging

from PySide6.QtCore import Qt, QSettings, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QPushButton,
                               QScrollArea, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from gui.pages.base_page import BasePage, card_layout, card_title
from gui.theme import (ACCENT, BG_DEEP, BORDER, DANGER, TEXT_MAIN, TEXT_MUTED,
                       WARN)  # noqa: F401
from qfluentwidgets import CardWidget, ComboBox, PrimaryPushButton, PushButton
from config.settings import QSETTINGS_APP, QSETTINGS_ORG


# U-06: 状态色唯一写者——所有 LED/run_status QSS 颜色经此统一入口，
# 避免 "#E64545 vs DANGER #FF6B6B" / "#FFB020 vs WARN #FFB454" 双源漂移。
# 备注：#E64545 / #FFB020 是历史更暗的红/琥珀；统一到 theme.DANGER / theme.WARN
# 之前用 _LED_FONT_SIZE 维持字号一致（U-05 已在 set_health_status 修过）。
_LED_FONT_SIZE = "12px"


def led_style(color, font_size=_LED_FONT_SIZE):
    """Build a single-line LED QSS string (U-06/U-05 收敛)。"""
    return f"color: {color}; font-size: {font_size};"


# 状态常量

STATUS_TEXT = {
    "pending": "待命",
    "running": "执行中",
    "done": "完成",
    "failed": "失败",
    "skipped": "跳过",
}
STATUS_COLOR = {
    # U-06: 全部从 theme 派生——单一色源；OK_DONE/SKIPPED 是新加的 token
    # （theme.py 已有 OK/ACCENT 但语义为"成功完成"，新加 OK_DONE 显式区分
    # 树节点绿色 vs 主色绿）。
    "pending": TEXT_MUTED,
    "running": ACCENT,
    "done": "#3BA55D",   # OK_DONE（成功完成——与 ACCENT 主色绿视觉区分）
    "failed": DANGER,
    "skipped": "#5A6B82",  # TEXT_SKIPPED（保留——独立 dim 灰）
}

CATEGORY_TEXT = {
    "F1": "F1 输入失败",
    "F1_TEMPLATE": "F1 模板未命中",
    "F1_PERMISSION": "F1 权限失败",
    "F1_EXEC": "F1 执行器异常",
    "F1_INTERNAL": "F1 内部错误",
    "F2": "F2 视觉失败",
    "F2_COORD": "F2 坐标异常",
    "F2_TIMEOUT": "F2 验证超时",
    "F3": "F3 决策漂移",
    "F4_VISION": "F4 视觉不可信",
    "F4_DARK": "F4 黑屏",
    "F4_WRONG_WINDOW": "F4 窗口错误",
    "F4_CONFLICT": "F4 观察通道冲突",
    "F4_LOW_CONF": "F4 置信度低",
    "F4_EXPIRED": "F4 证据过期",
    "F4_NOT_VERIFIED": "F4 未验证",
    "F4_FRAME": "F4 帧异常",
    "F5_ACTION_BLOCK": "F5 动作被策略拦截",
    "F5_RISK_HIGH": "F5 风险过高",
    "EMERGENCY": "紧急人工介入",
}


def category_text(code):
    return CATEGORY_TEXT.get(code, f"{code}" if code else "unknown")


def _status_color(status):
    """统一查 STATUS_COLOR（键：pending/running/done/failed/skipped）——
    runtime 的 target_progress 发的是 done，不是 succeeded（曾致完成目标不显示绿色）。"""
    from PySide6.QtGui import QColor
    return QColor(STATUS_COLOR.get(status, "#7A90B0"))


# 树列
TREE_COL_TARGET = 0
TREE_COL_STATUS = 1

# 统一时间线颜色（全部取 theme token——U-36 单一色源）
_TIMELINE_COLORS = {
    "run_started": ACCENT,
    "target_progress": ACCENT,
    "pause_requested": WARN,
    "human_intervention": DANGER,
    "fail_recorded": DANGER,
}

# 预设（勾选组合）持久化分组——沿用本页既有的 QSettings(QSETTINGS_ORG, QSETTINGS_APP) 通道
_PRESET_GROUP = "deck_presets"


def _timeline_color(event_type):
    from PySide6.QtGui import QColor
    return QColor(_TIMELINE_COLORS.get(event_type, TEXT_MAIN))


# 实时观测卡片

class ObservationSnapshot(CardWidget):
    _FRAME_STYLE = (
        f"background: {BG_DEEP}; border: 1px dashed {BORDER}; border-radius: 8px;"
        f"color: {TEXT_MUTED};")

    def __init__(self, parent=None):
        super().__init__(parent)
        card_title(self, "实时画面（每 3 秒自动刷新）")
        self._shot = QLabel("游戏画面（待真机接入）")
        self._shot.setAlignment(Qt.AlignCenter)
        self._shot.setMinimumHeight(200)
        self._shot.setStyleSheet(self._FRAME_STYLE)
        card_layout(self).addWidget(self._shot)
        self._body = QLabel("—")
        self._body.setWordWrap(True)
        # U-06: 统一 TEXT_MUTED
        self._body.setStyleSheet(f"color: {TEXT_MUTED};")
        card_layout(self).addWidget(self._body)
        # 手动抓帧（observation.py 的「抓取画面」能力等价承接——自动刷新之外
        # 的即时抓拍入口）
        cap_row = QHBoxLayout()
        self.capture_btn = QPushButton("抓取画面")
        self.capture_btn.setToolTip("立即抓取一帧游戏画面（自动刷新周期 3 秒）")
        cap_row.addWidget(self.capture_btn)
        cap_row.addStretch(1)
        card_layout(self).addLayout(cap_row)
        self._history = []

    def set_frame(self, pixmap_or_none):
        # 审查根因：本环境 QLabel.setPixmap 实测失效（pixmap() 返回 null）——
        # 统一走样式 background-image（pixmap 存临时文件，与观察中心共用）。
        # D7（S2-B）：共享助手 gui.pages.live_frame.save_live_frame()——消除
        # command_deck.py + observation.py 两处 parent.parent.parent 硬编码
        # + 重复 mkdir/save 逻辑。
        self._shot.clear()
        if pixmap_or_none is not None:
            try:
                from gui.pages.live_frame import save_live_frame
                tmp = save_live_frame(pixmap_or_none)
                self._shot.setStyleSheet(
                    f"background-image: url({tmp.as_posix()}); background-repeat: no-repeat;"
                    f"background-position: center; background-color: {BG_DEEP};")
            except Exception as e:
                # 静默吞 → 用户看到占位无报错；此处日志 + 占位文案不变
                logging.getLogger("gui.command_deck").warning(
                    "实时画面写入失败: %s: %s", type(e).__name__, e)
                self._shot.setStyleSheet(self._FRAME_STYLE)
                self._shot.setPixmap(pixmap_or_none)  # 兜底
        else:
            self._shot.setStyleSheet(self._FRAME_STYLE)
            self._shot.setText("游戏画面（待真机接入）")

    def update_snapshot(self, observer, target, confidence, room):
        lines = [f"目标：{target}",
                 f"识别：{observer}",
                 f"置信：{confidence:.0%}" if confidence is not None else "置信：--"]
        if room:
            lines.append(f"区域：{room}")
        snapshot = "\n".join(lines)
        self._body.setText(snapshot)
        # U-06: 强调文本（区域+置信度）用 TEXT_MAIN——之前 #E8F0FE 接近 TEXT_MAIN 但色值漂移
        self._body.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 13px;")
        self._push(snapshot)

    def text(self):
        return self._body.text()

    def history_text(self, last=5):
        return "\n".join(self._history[-last:])

    def _push(self, text):
        self._history.append(text)
        self._history = self._history[-20:]


# 健康栏

HEALTH_LABELS = {
    "window": "窗口",
    "foreground": "前台",
    "admin": "提权",
    "capture": "截屏",
    "ocr": "OCR",
    "input": "输入权限",
}


class RuntimeHealthBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._items = {}
        for key, label in HEALTH_LABELS.items():
            box = QLabel(f"[?] {label}")
            # U-06: 统一 TEXT_MUTED + BORDER token
            box.setStyleSheet(
                f"color: {TEXT_MUTED}; font-size: 11px; border: 1px solid {BORDER};"
                "border-radius: 4px; padding: 2px 8px;")
            lay.addWidget(box)
            self._items[key] = box
        lay.addStretch(1)

    def set_health(self, health):
        for key, ok in health.items():
            box = self._items.get(key)
            if box is None:
                continue
            if key == "input" and ("input_l0" in health or "input_l1" in health):
                l0 = "L0✓" if health.get("input_l0") else "L0✗"
                l1 = "L1✓" if health.get("input_l1") else "L1✗"
                l2 = "L2?" if health.get("input_l2") is None else ("L2✓" if health.get("input_l2") else "L2✗")
                mark = "✓" if ok else "✗"
                # 颜色沿用既有语义：OK_DONE（STATUS_COLOR["done"]）绿色 / DANGER 红
                color = STATUS_COLOR["done"] if ok else DANGER
                box.setText(f"[{mark}] 输入 {l0} {l1} {l2}")
                box.setStyleSheet(f"color: {color}; font-size: 11px; border: 1px solid {BORDER};"
                                  "border-radius: 4px; padding: 2px 8px;")
                continue
            mark = "✓" if ok else "✗"
            color = STATUS_COLOR["done"] if ok else DANGER
            label = HEALTH_LABELS.get(key, key)
            box.setText(f"[{mark}] {label}")
            box.setStyleSheet(f"color: {color}; font-size: 11px; border: 1px solid {BORDER};"
                              "border-radius: 4px; padding: 2px 8px;")


# 失败检查器

class FailureInspector(CardWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        card_title(self, "失败检查器")
        self._body = QLabel("尚无失败记录 —")
        self._body.setWordWrap(True)
        # U-06: 统一 TEXT_MUTED
        self._body.setStyleSheet(f"color: {TEXT_MUTED};")
        card_layout(self).addWidget(self._body)
        card_layout(self).addStretch(1)
        self.history = []

    def on_failure(self, run_no, state, observation, action, input_info, reason, category=None):
        cat_text = f"[{category}] " if category else ""
        lines = [
            f"Run #{run_no}  FAILED  {cat_text}",
            f"State: {state or '—'}",
            f"Last observation: {observation or '—'}",
            f"Action: {action or '—'}",
            f"Input: {input_info or '—'}",
            f"Reason: {reason or '—'}",
        ]
        self.history.append((run_no, state, reason, category))
        self.history = self.history[-20:]
        recent = [
            f"#{r} [{cat or '-'}] {st or '-'} → {rr or '-'}"
            for r, st, rr, cat in self.history[-5:]
        ]
        self._body.setText("\n".join(lines + [""] + recent))
        # U-06: 失败正文用 DANGER 红色
        self._body.setStyleSheet(f"color: {DANGER};")

    def reset(self):
        self._body.setText("尚无失败记录 —")
        self._body.setStyleSheet(f"color: {TEXT_MUTED};")


# 人机横幅（P1 上半区）

class PauseBanner(CardWidget):
    """人机横幅：显示暂停原因 + 继续/跳过。

    由 runtime 的 pause_requested / human_intervention 事件驱动（本页 on_event
    调用 show_reason），默认隐藏——无人工介入时不占版面。
    """

    continue_clicked = Signal()
    skip_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        card_title(self, "人机介入")
        self.reason_label = QLabel("—")
        self.reason_label.setWordWrap(True)
        self.reason_label.setStyleSheet(f"color: {WARN}; font-size: 13px;")
        card_layout(self).addWidget(self.reason_label)
        row = QHBoxLayout()
        self.continue_btn = QPushButton("继续")
        self.continue_btn.setToolTip("从暂停处继续执行")
        self.skip_btn = QPushButton("跳过")
        self.skip_btn.setToolTip("跳过剩余目标，本轮直接结束")
        row.addWidget(self.continue_btn)
        row.addStretch(1)
        row.addWidget(self.skip_btn)
        card_layout(self).addLayout(row)
        self.continue_btn.clicked.connect(self.continue_clicked)
        self.skip_btn.clicked.connect(self.skip_clicked)
        self.setVisible(False)

    def show_reason(self, reason):
        self.reason_label.setText(reason or "人工介入")
        self.setVisible(True)

    def clear(self):
        self.reason_label.setText("—")
        self.setVisible(False)


# 统一时间线（P1 中部区）

class TimelinePanel(CardWidget):
    """统一时间线：事件统计 + 失败检查器 + 事件流（三者合并为一处）。

    observation.py 的两项能力（事件类型计数统计 / 时间线追加）由 CommandDeck
    .on_event 写入本面板持有的控件，不依赖 ObservationPage 实例。
    """

    MAX_ITEMS = 50

    def __init__(self, parent=None):
        super().__init__(parent)
        card_title(self, "统一时间线")
        # 事件统计（observation 能力①）
        self.stats_label = QLabel("尚未开始任务 — 事件统计将显示在这里")
        self.stats_label.setWordWrap(True)
        self.stats_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 13px;")
        card_layout(self).addWidget(self.stats_label)
        # 失败检查器（原失败卡并入此处）
        self.inspector = FailureInspector()
        card_layout(self).addWidget(self.inspector)
        # 事件流（observation 能力②，上限 50 条）
        self.timeline = QListWidget()
        self.timeline.setMaximumHeight(200)
        self.timeline.setMinimumHeight(140)
        self.timeline.setStyleSheet(
            f"background: {BG_DEEP}; border: 1px solid {BORDER}; border-radius: 8px;"
            f"color: {TEXT_MAIN}; font-size: 12px;")
        card_layout(self).addWidget(self.timeline)

    # 计数/追加的实现留在 CommandDeck.on_event（契约要求"由本页 on_event 承接"），
    # 这里只提供控件与容量上限。
    def max_items(self):
        return self.MAX_ITEMS


# 出击页主页面

class CommandDeck(BasePage):
    run_requested = Signal(list)
    stop_requested = Signal()

    def __init__(self, targets, parent=None):
        super().__init__("出击", parent)
        self._run_no = 0
        self._last_state = None
        self._targets = targets or []
        # 事件统计（observation 能力①）：{event_type: count}
        self._counts = {}
        # 勾选状态：tid → bool（用户显式改过的才记录；缺省 = 未完成则勾选）
        self._check_state = {}
        self._done_cache = set()
        self._rebuilding = False
        self._user_paused = False
        # 录制（世界图迁入）
        self._recorder = None
        self._rec_hud = None
        self._rec_hud_own = False
        self._countdown_timer = None

        # Header：状态 LED
        self.led = QLabel("● 空闲")
        self.led.setStyleSheet(led_style(TEXT_MUTED))
        self._header.layout().addWidget(self.led)
        # U-21: 三个状态源（header LED / header 右侧 status_label / 15px run_status）
        # 信息重复且更新节奏不同。收敛为 LED（状态） + run_status（进程/进度）两个；
        # BasePage 自带的 status_label 不再使用 → 隐藏（保持 BasePage 框架兼容）。
        self.status_label.setVisible(False)
        self.set_status("")

        # 内容过长（上半/中部/下半三段）→ 滚动容器，避免窗口高度不足时裁切
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._scroll.viewport().setStyleSheet("background: transparent;")
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(10)
        self._scroll.setWidget(body)
        self.content_layout.addWidget(self._scroll, 1)

        # 上半区：控制栏
        # U-32: 不再覆盖 CardWidget 内部样式——交给 CardWidget 默认皮肤，
        # 悬停/圆角效果才不丢。
        control_card = CardWidget()
        control_layout = QHBoxLayout(control_card)
        control_layout.setContentsMargins(16, 12, 16, 12)
        control_layout.setSpacing(12)

        self.target_combo = ComboBox()
        self._map_groups = {}
        self._combo_data = []  # 每项结构化数据 (kind, payload)，防同名串区
        self._rebuild_combo()
        self.start_btn = PrimaryPushButton("开始任务")
        self.start_btn.setFixedWidth(120)
        self.start_btn.setToolTip("只执行目标树中已勾选的点位")
        self.pause_btn = PushButton("暂停")
        self.pause_btn.setFixedWidth(70)
        self.pause_btn.setToolTip("暂停当前任务，再点一次继续")
        self.stop_btn = PushButton("停止")
        self.stop_btn.setEnabled(False)
        self.record_btn = PushButton("● 录制轨迹")
        self.record_btn.setToolTip("录制你的手动操作（WASD / 视角 / 点击）并保存为轨迹\n"
                                   "回放时可复现同一路线——机关与复杂路径用这个最可靠\n"
                                   "按 F10 或再次点击按钮结束录制并保存")

        control_layout.addWidget(QLabel("目标："))
        control_layout.addWidget(self.target_combo)
        control_layout.addStretch(1)
        control_layout.addWidget(self.record_btn)
        control_layout.addWidget(self.pause_btn)
        control_layout.addWidget(self.stop_btn)
        control_layout.addWidget(self.start_btn)
        content.addWidget(control_card)

        # 运行状态行
        self.run_status = QLabel("● 空闲 — 勾选目标后开始")
        self.run_status.setStyleSheet(
            f"font-size: 15px; font-weight: 700; color: {TEXT_MUTED};")
        content.addWidget(self.run_status)

        # 上半区：目标树 + 人机横幅
        upper = QHBoxLayout()
        queue_card = CardWidget()
        card_title(queue_card, "出击目标（勾选后执行）")
        tool_row = QHBoxLayout()
        self.check_all_btn = PushButton("全选")
        self.check_all_btn.setToolTip("勾选全部目标（含已完成——完成后可强制重跑）")
        self.check_none_btn = PushButton("清空勾选")
        self.check_pending_btn = PushButton("仅未完成")
        self.check_pending_btn.setToolTip("只勾选未完成目标（已完成项默认就跳过）")
        self.check_count = QLabel("已勾选 0/0")
        self.check_count.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        tool_row.addWidget(self.check_all_btn)
        tool_row.addWidget(self.check_none_btn)
        tool_row.addWidget(self.check_pending_btn)
        tool_row.addStretch(1)
        tool_row.addWidget(self.check_count)
        card_layout(queue_card).addLayout(tool_row)

        self.target_tree = QTreeWidget()
        self.target_tree.setHeaderLabels(["目标", "状态"])
        self.target_tree.setAlternatingRowColors(True)
        self.target_tree.setMinimumHeight(180)
        # U-18: 显式缩进——节点文本不再加前导空格，层级由 setIndentation 控制
        self.target_tree.setIndentation(14)
        self.target_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.rows = {}
        self._map_nodes = {}
        self._region_nodes = {}
        # 完成状态（chest_done）先取——首次建树即灰显已完成点位
        self._refresh_done_cache()
        # 队列树跟随目标选择动态过滤（初始=全部目标）
        self._rebuild_tree([t["id"] for t in self._targets])
        self.target_combo.currentIndexChanged.connect(self._on_target_filter)
        self.target_tree.itemChanged.connect(self._on_tree_item_changed)
        self.target_tree.customContextMenuRequested.connect(self._on_tree_menu)
        self.target_tree.expandToDepth(1)
        card_layout(queue_card).addWidget(self.target_tree)
        upper.addWidget(queue_card, 2)

        # 人机横幅（暂停原因 + 继续/跳过；无介入时隐藏）
        self.banner = PauseBanner()
        self.banner.continue_clicked.connect(self._on_banner_continue)
        self.banner.skip_clicked.connect(self._on_banner_skip)
        upper.addWidget(self.banner, 1)
        content.addLayout(upper, 2)

        # 中部区：实时画面 + 统一时间线
        middle = QHBoxLayout()
        self.snapshot = ObservationSnapshot()
        self.snapshot.capture_btn.clicked.connect(self._manual_capture)
        middle.addWidget(self.snapshot, 3)

        panel = TimelinePanel()
        self.timeline_panel = panel
        self._stats = panel.stats_label
        self.inspector = panel.inspector
        self.timeline = panel.timeline
        middle.addWidget(panel, 2)
        content.addLayout(middle, 2)

        # 下半区：健康条 + 预设卡
        health_row = QHBoxLayout()
        self.health_toggle = PushButton("系统状态 ▼")
        self.health_toggle.setFixedWidth(110)
        self.health_toggle.clicked.connect(self._toggle_health)
        health_row.addWidget(self.health_toggle)
        self.health_bar = RuntimeHealthBar()
        health_row.addWidget(self.health_bar, 1)
        content.addLayout(health_row)

        lower = QHBoxLayout()
        self._build_preset_card(lower)
        content.addLayout(lower, 1)

        # 信号
        self.start_btn.clicked.connect(self._on_start)
        self.pause_btn.clicked.connect(self._on_pause)
        self.stop_btn.clicked.connect(self.stop_requested)
        self.record_btn.clicked.connect(self._toggle_record)
        self.check_all_btn.clicked.connect(lambda: self._check_rows("all"))
        self.check_none_btn.clicked.connect(lambda: self._check_rows("none"))
        self.check_pending_btn.clicked.connect(lambda: self._check_rows("pending"))

        # 实时观测：主线程同步抓帧（截图仅 0.12s），3s 周期，仅页面可见时
        self._snapshot_refresh = QTimer(self)
        self._snapshot_refresh.setInterval(3000)
        self._snapshot_refresh.timeout.connect(self._refresh_snapshot)
        self._snapshot_refresh.start()
        QTimer.singleShot(800, self._refresh_snapshot)

    # 下半区构件

    def _build_preset_card(self, parent_layout):
        """预设卡：QSettings(QSETTINGS_ORG, QSETTINGS_APP) 存/取勾选组合。"""
        card = CardWidget()
        card_title(card, "预设（勾选组合）")
        row = QHBoxLayout()
        row.addWidget(QLabel("组合："))
        self.preset_combo = QComboBox()
        row.addWidget(self.preset_combo, 1)
        card_layout(card).addLayout(row)
        row2 = QHBoxLayout()
        self.preset_name = QLineEdit()
        self.preset_name.setPlaceholderText("预设名（留空自动编号）")
        self.preset_name.setFixedWidth(150)
        self.preset_save_btn = QPushButton("保存勾选")
        self.preset_apply_btn = QPushButton("应用")
        self.preset_del_btn = QPushButton("删除")
        row2.addWidget(self.preset_name)
        row2.addWidget(self.preset_save_btn)
        row2.addWidget(self.preset_apply_btn)
        row2.addWidget(self.preset_del_btn)
        row2.addStretch(1)
        card_layout(card).addLayout(row2)
        self.preset_hint = QLabel("—")
        self.preset_hint.setWordWrap(True)
        self.preset_hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        card_layout(card).addWidget(self.preset_hint)
        self.preset_save_btn.clicked.connect(self._save_preset)
        self.preset_apply_btn.clicked.connect(self._apply_preset)
        self.preset_del_btn.clicked.connect(self._delete_preset)
        parent_layout.addWidget(card)
        self._reload_presets()

    # 折叠/开关

    def _toggle_health(self):
        vis = not self.health_bar.isVisible()
        self.health_bar.setVisible(vis)
        self.health_toggle.setText("系统状态 ▲" if vis else "系统状态 ▼")

    # 实时画面

    def _refresh_snapshot(self, force=False):
        """抓一帧并送到实时观测卡片（同步，见 ObservationPage._capture 同款根因）。"""
        if not force and not self.isVisible():
            return
        try:
            import numpy as np
            from PIL import Image as PILImage
            from PIL.ImageQt import ImageQt
            from PySide6.QtGui import QPixmap
            from runtime.drivers.local.vision import ScreenVision
            vision = ScreenVision()
            shot = vision.take_screenshot()
            if shot is None:
                self.snapshot.set_frame(None)
                return
            img = shot[0]
            arr = np.asarray(img).copy()
            pil = PILImage.fromarray(arr, "RGB") \
                if arr.ndim == 3 and arr.shape[2] == 3 else PILImage.fromarray(arr)
            pix = QPixmap.fromImage(ImageQt(pil))
            w = self.snapshot._shot.width() or 400
            h = self.snapshot._shot.height() or 180
            self.snapshot.set_frame(pix.scaled(
                w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception as e:
            # 静默吞（原 except 无 log / 无用户可见反馈）→ 真机断开时用户
            # 只看到占位图，以为程序正常。此处记日志；占位文案由 set_frame(None) 设置。
            logging.getLogger("gui.command_deck").warning(
                "实时画面刷新失败: %s: %s", type(e).__name__, e)
            self.snapshot.set_frame(None)

    def _manual_capture(self):
        """手动抓帧（observation.py「抓取画面」能力等价承接）。"""
        btn = self.snapshot.capture_btn
        btn.setEnabled(False)
        btn.setText("抓取中…")
        try:
            self._refresh_snapshot(force=True)
        finally:
            btn.setEnabled(True)
            btn.setText("抓取画面")

    # 状态写者（U-02 单一写者：start_btn/stop_btn 只被这四处改动）

    def set_starting(self):
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.led.setText("● 初始化")
        # U-06: 统一走 theme.WARN（之前是 #FFB020 与 WARN #FFB454 双源；
        # 选用 WARN 是"初始化"与"拦截/无目标"同语义——"需要关注但未失败"）。
        self.led.setStyleSheet(led_style(WARN))

    def set_stopping(self):
        self.stop_btn.setEnabled(False)
        self.led.setText("● 停止中")
        self.led.setStyleSheet(led_style(WARN))

    def set_health_status(self, text, busy=False):
        self.led.setText("● " + text)
        # U-05: keep font-size: 12px consistent with every other LED branch
        # (set_starting/set_stopping/on_event branches) to avoid the LED text
        # visibly jumping 1-2px every 5s when health flips between busy/idle.
        # U-06: 颜色经 led_style 统一入口——token 源在 theme.WARN / theme.TEXT_MUTED。
        self.led.setStyleSheet(led_style(WARN) if busy else led_style(TEXT_MUTED))
        # U-02（u6 终审收尾）：start_btn 不在此写入——健康 5s 周期刷新会
        # 在任务运行中把按钮重新点亮（点击被状态机静默吞掉，且 LED/G3 拦截
        # 原因被覆盖抖动）。状态机单写者：set_starting / set_stopping /
        # set_run_status / on_event("run_finished") / 状态恢复 _sync_runtime_state
        # 是 start_btn/stop_btn 的唯一切换点；健康路径只动 LED。

    def set_health(self, health):
        self.health_bar.set_health(health)

    def set_run_status(self, text, busy=False):
        self.run_status.setText(text)
        # U-06: 颜色全部走 theme——ACCENT=主色绿, TEXT_MUTED=空闲灰, WARN=关注琥珀
        # （之前 #FFB020 与 WARN #FFB454 双源；run_status 是 15px 大字，统一到 WARN）。
        if busy:
            color = ACCENT
        elif "空闲" in text:
            color = TEXT_MUTED
        else:
            color = WARN
        self.run_status.setStyleSheet(
            f"font-size: 15px; font-weight: 700; color: {color};")

    # 目标选择 / 勾选

    def _rebuild_combo(self):
        """重建目标下拉（全部目标 + 各大地图）——刷新 targets 后调用。"""
        self._map_groups = {}
        self._combo_data = []
        self.target_combo.clear()
        # 目标选择只到地图级（全部目标 / 各大地图）——区域/点位选择在任务队列树里
        self._combo_data.append(("all", None))
        combo_items = ["全部目标"]
        for t in self._targets:
            map_name = t.get("map_name") or "未分组"
            if map_name not in self._map_groups:
                self._map_groups[map_name] = []
                combo_items.append(map_name)
                self._combo_data.append(("map", map_name))
            self._map_groups[map_name].append(t)
        self.target_combo.addItems(combo_items)

    def refresh_targets(self, new_targets):
        """外部刷新目标数据（录制同步后指挥台联动）——重建 combo + 队列树。"""
        self._targets = new_targets or []
        self._rebuild_combo()
        self._refresh_done_cache()
        self._rebuild_tree([t["id"] for t in self._targets])
        self.target_tree.expandToDepth(1)
        self.run_status.setText(f"● 已刷新 {len(self._targets)} 个目标")

    def _resolve_selection(self, idx):
        """按 itemData 精确解析（当前仅 all/地图 两级）。"""
        data = self._combo_data[idx] if 0 <= idx < len(self._combo_data) else None
        if data is None:
            return []
        kind, payload = data
        if kind == "all":
            return [t["id"] for t in self._targets]
        if kind == "map":
            return [t["id"] for t in self._map_groups.get(payload, [])]
        return []

    def _on_target_filter(self, idx):
        """目标下拉变化 → 队列树动态过滤（只显示选中地图/全部）。"""
        self._rebuild_tree(self._resolve_selection(idx))
        self.target_tree.expandToDepth(1)

    def _rebuild_tree(self, ids):
        """按目标 id 集合重建队列树（地图 → 区域 → 点位，状态行待命）。

        自定义地图（map_name="自定义"）跳过区域级——点位直接挂地图节点
        （用户要求：不要 地图>区域>点位 三级，只要 自定义>自定义-N 两级）。

        U-18: 节点文本不再加前导空格（QTreeWidget 自带 setIndentation
        控制层级缩进——再加空格会出现"双缩进"，自定义地图两级与普通
        地图三级的视觉间距也不一致）。缩进交给 self.target_tree.setIndentation
        （构造函数中已设 14）。

        P1（三页重做）：点位行带 checkbox（只跑勾中项）；chest_done 的点位
        灰显 + 默认不勾（跳过），右键「标记未完成」可重跑。地图/区域节点
        仅作分组头，不带 checkbox。
        """
        self._rebuilding = True
        try:
            self.target_tree.clear()
            self.rows = {}
            self._map_nodes = {}
            self._region_nodes = {}
            want = set(ids)
            for t in self._targets:
                if t["id"] not in want:
                    continue
                map_name = t.get("map_name") or "未分组"
                if map_name not in self._map_nodes:
                    node = QTreeWidgetItem([map_name, ""])
                    node.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    self.target_tree.addTopLevelItem(node)
                    self._map_nodes[map_name] = node
                name = t.get("name") or t["id"]
                tid = t["id"]
                leaf = QTreeWidgetItem([name, STATUS_TEXT.get("pending", "待命")])
                leaf.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable
                              | Qt.ItemIsUserCheckable)
                leaf.setData(TREE_COL_TARGET, Qt.UserRole, tid)
                done = tid in self._done_cache
                if done:
                    # chest_done：灰显 + 默认不勾（跳过）——灰 = TEXT_MUTED（既有 token）
                    muted = _status_color("pending")
                    leaf.setText(TREE_COL_STATUS, STATUS_TEXT["done"])
                    leaf.setForeground(TREE_COL_TARGET, muted)
                    leaf.setForeground(TREE_COL_STATUS, muted)
                    leaf.setToolTip(
                        TREE_COL_TARGET,
                        "已完成（chest_done）——默认跳过；\n"
                        "右键「标记未完成」或手动勾选可重跑")
                    default_checked = False
                else:
                    leaf.setText(TREE_COL_STATUS, STATUS_TEXT.get("pending", "待命"))
                    leaf.setForeground(TREE_COL_STATUS, _status_color("pending"))
                    default_checked = True
                checked = self._check_state.get(tid, default_checked)
                leaf.setCheckState(TREE_COL_TARGET,
                                   Qt.Checked if checked else Qt.Unchecked)
                # 自定义地图：点位直接挂地图下（无区域级）
                if map_name == "自定义":
                    self._map_nodes[map_name].addChild(leaf)
                else:
                    region = t.get("room") or t.get("region") or "未知区域"
                    rkey = (map_name, region)
                    if rkey not in self._region_nodes:
                        rnode = QTreeWidgetItem([region, ""])
                        rnode.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                        self._map_nodes[map_name].addChild(rnode)
                        self._region_nodes[rkey] = rnode
                    self._region_nodes[rkey].addChild(leaf)
                self.rows[tid] = leaf
        finally:
            self._rebuilding = False
        self._update_check_count()

    def _on_tree_item_changed(self, item, column):
        """勾选变化 → 记状态（跨过滤/刷新保持）+ 刷新计数。"""
        if self._rebuilding or column != TREE_COL_TARGET:
            return
        tid = item.data(TREE_COL_TARGET, Qt.UserRole)
        if not tid:
            return
        self._check_state[tid] = item.checkState(TREE_COL_TARGET) == Qt.Checked
        self._update_check_count()

    def _on_tree_menu(self, pos):
        """右键菜单：勾选/取消勾选、标记未完成（重跑）、标记已完成（跳过）。"""
        item = self.target_tree.itemAt(pos)
        if item is None:
            return
        tid = item.data(TREE_COL_TARGET, Qt.UserRole)
        if not tid:
            return
        menu = QMenu(self.target_tree)
        checked = item.checkState(TREE_COL_TARGET) == Qt.Checked
        act_toggle = menu.addAction("取消勾选" if checked else "勾选（加入本轮）")
        menu.addSeparator()
        act_undone = menu.addAction("标记未完成（可重跑）")
        act_done = menu.addAction("标记已完成（跳过）")
        chosen = menu.exec(self.target_tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_toggle:
            item.setCheckState(TREE_COL_TARGET,
                               Qt.Unchecked if checked else Qt.Checked)
        elif chosen == act_undone:
            self.mark_undone(tid)
        elif chosen == act_done:
            self.mark_done(tid)

    def _check_rows(self, mode):
        """勾选工具：all=全选 / none=清空 / pending=仅未完成（作用于全量目标）。"""
        for t in self._targets:
            tid = t["id"]
            if mode == "all":
                val = True
            elif mode == "none":
                val = False
            else:
                val = tid not in self._done_cache
            self._check_state[tid] = val
        self._rebuilding = True
        try:
            for tid, leaf in self.rows.items():
                val = self._check_state.get(tid, True)
                leaf.setCheckState(TREE_COL_TARGET,
                                   Qt.Checked if val else Qt.Unchecked)
        finally:
            self._rebuilding = False
        self._update_check_count()

    def _checked_ids(self):
        """已勾选目标 id（跨地图过滤保持——只跑勾中项）。"""
        out = []
        for t in self._targets:
            tid = t["id"]
            leaf = self.rows.get(tid)
            if leaf is not None:
                checked = leaf.checkState(TREE_COL_TARGET) == Qt.Checked
            else:
                checked = self._check_state.get(
                    tid, tid not in self._done_cache)
            if checked:
                out.append(tid)
        return out

    def _update_check_count(self):
        try:
            total = len(self._targets)
            self.check_count.setText(f"已勾选 {len(self._checked_ids())}/{total}")
        except Exception:
            pass

    # 完成状态（chest_done）

    def _pkg_key(self):
        """完成状态域键——与 MissionController._pkg_key 同源推导
        （知识包路径 sha256[:12]；MissionController 用 str(ROOT/"knowledge/
        source/black_tower_test") 构造 knowledge_dir，此处保持同一字面量，
        换知识包时两处同步）。"""
        try:
            import hashlib
            from config.settings import ROOT
            p = ROOT / "knowledge" / "source" / "black_tower_test"
            return hashlib.sha256(str(p).encode()).hexdigest()[:12]
        except Exception:
            logging.getLogger("gui.command_deck").exception("pkg_key 推导失败")
            return None

    def _done_ids(self):
        """已完成（chest_done）目标 id。

        有主窗口时完全以 mission_controller 的公开 API 为准（含空集合——它自己
        管 pkg_key，切地图后状态域随之切换，本页不重复推导）；无主窗口（离屏
        构造/单页测试）才兜底直查 runtime.chest_state。
        """
        known = {t["id"] for t in self._targets}
        try:
            mw = self.window()
            mc = getattr(mw, "mission_controller", None) if mw is not None else None
            if mc is not None and hasattr(mc, "completed_targets"):
                done = {str(x) for x in (mc.completed_targets() or [])}
                return done & known if known else set()
        except Exception:
            logging.getLogger("gui.command_deck").exception("完成状态读取失败")
        done = set()
        key = self._pkg_key()
        if key:
            try:
                import runtime.chest_state as cs
                done |= {tid for tid in known if cs.is_done(key, tid)}
            except Exception:
                logging.getLogger("gui.command_deck").exception(
                    "chest_state 查询失败")
        return done & known if known else set()

    def _refresh_done_cache(self):
        self._done_cache = self._done_ids()
        return self._done_cache

    def mark_undone(self, target_id):
        """标记未完成（右键菜单/F10 后重跑）——撤销 chest_done 并勾上该点位。"""
        key = self._pkg_key()
        if key:
            try:
                import runtime.chest_state as cs
                cs.mark_undone(key, target_id)
            except Exception:
                logging.getLogger("gui.command_deck").exception(
                    "mark_undone 失败: %s", target_id)
        self._done_cache.discard(target_id)
        self._check_state[target_id] = True
        self._rebuild_tree(self._resolve_selection(self.target_combo.currentIndex()))
        self.set_run_status(f"● 已标记 {target_id} 未完成（可重跑）")

    def mark_done(self, target_id):
        """标记已完成（跳过本轮）——写入 chest_done 并灰显该点位。"""
        key = self._pkg_key()
        if key:
            try:
                import runtime.chest_state as cs
                cs.mark_done(key, target_id)
            except Exception:
                logging.getLogger("gui.command_deck").exception(
                    "mark_done 失败: %s", target_id)
        self._done_cache.add(target_id)
        self._check_state[target_id] = False
        self._rebuild_tree(self._resolve_selection(self.target_combo.currentIndex()))
        self.set_run_status(f"● 已标记 {target_id} 完成（本轮跳过）")

    # 预设（QSettings）

    def _preset_names(self):
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        s.beginGroup(_PRESET_GROUP)
        names = sorted(s.childKeys())
        s.endGroup()
        return names

    def _load_preset(self, name):
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        raw = s.value(f"{_PRESET_GROUP}/{name}", "")
        if not raw:
            return []
        try:
            return [str(x) for x in json.loads(str(raw))]
        except Exception:
            logging.getLogger("gui.command_deck").exception(
                "预设解析失败: %s", name)
            return []

    def _reload_presets(self, select=None):
        names = self._preset_names()
        self.preset_combo.clear()
        self.preset_combo.addItems(names)
        if select and select in names:
            self.preset_combo.setCurrentText(select)
        self.preset_hint.setText(
            f"共 {len(names)} 个预设" if names
            else "尚无预设——勾选目标后点「保存勾选」")

    def _save_preset(self):
        name = self.preset_name.text().strip()
        if not name:
            name = f"预设-{len(self._preset_names()) + 1}"
        ids = self._checked_ids()
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        s.setValue(f"{_PRESET_GROUP}/{name}", json.dumps(ids, ensure_ascii=False))
        s.sync()
        if s.status() != QSettings.Status.NoError:
            # 环境限制（如注册表写入被拒）不得静默假装成功
            self.preset_hint.setText(
                f"保存失败：QSettings 写入受限（{s.status().name}）——预设未落盘")
            return
        self._reload_presets(select=name)
        self.preset_hint.setText(f"已保存「{name}」：{len(ids)} 个目标")

    def _apply_preset(self):
        name = self.preset_combo.currentText()
        if not name:
            self.preset_hint.setText("没有可应用的预设")
            return
        ids = set(self._load_preset(name))
        known = {t["id"] for t in self._targets}
        for t in self._targets:
            self._check_state[t["id"]] = t["id"] in ids
        self._rebuild_tree(self._resolve_selection(self.target_combo.currentIndex()))
        msg = f"已应用「{name}」：{len(ids & known)} 个目标"
        unknown = ids - known
        if unknown:
            msg += f"（{len(unknown)} 个已不存在）"
        self.preset_hint.setText(msg)

    def _delete_preset(self):
        name = self.preset_combo.currentText()
        if not name:
            self.preset_hint.setText("没有可删除的预设")
            return
        s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        s.remove(f"{_PRESET_GROUP}/{name}")
        s.sync()
        self._reload_presets()
        if s.status() != QSettings.Status.NoError:
            self.preset_hint.setText(
                f"删除失败：QSettings 写入受限（{s.status().name}）")
            return
        self.preset_hint.setText(f"已删除「{name}」")

    # 目标执行

    def find_region_index(self, english_region):
        """英文区域 id → 下拉框 index。目标选择已收敛为地图级（区域不进 combo），
        执行由 _on_guide_run 直接走 matched——返回 None 表示不切换 combo。"""
        return None

    def _on_start(self):
        """开始任务：只把勾选中的目标交给 main_window（run_requested）。"""
        targets = self._checked_ids()
        if not targets:
            self.led.setText("● 没有勾选任何目标（请在目标树勾选后重试）")
            # U-06: 统一到 DANGER——之前 #E64545 与 DANGER #FF6B6B 视觉上几乎
            # 等价，但 DANGER 是项目 token；此处"目标不存在"是用户级错误。
            self.led.setStyleSheet(led_style(DANGER))
            self.set_run_status("● 无勾选目标——不能开始")
            return
        self.run_requested.emit(targets)

    def _invoke_runtime(self, method_name, *args, **kwargs):
        """调用 window().api 上的 runtime 方法（无窗口/无 api → False，绝不抛）。"""
        try:
            mw = self.window()
            api = getattr(mw, "api", None) if mw is not None else None
            fn = getattr(api, method_name, None)
            if callable(fn):
                fn(*args, **kwargs)
                return True
        except Exception:
            logging.getLogger("gui.command_deck").exception(
                "runtime 调用失败: %s", method_name)
        return False

    def _resume_runtime(self):
        """继续：api.resume()；若已无运行线程则落回可重启状态（防状态卡 running）。"""
        ok = self._invoke_runtime("resume")
        if ok:
            try:
                mw = self.window()
                api = getattr(mw, "api", None) if mw is not None else None
                th = getattr(api, "_thread", None)  # 只读探测线程存活
                if api is not None and (th is None or not th.is_alive()):
                    stop = getattr(api, "stop", None)
                    if callable(stop):
                        stop()  # 公开 API：状态落到可重启的 stopped
            except Exception:
                logging.getLogger("gui.command_deck").exception(
                    "继续后状态复位失败")
        return ok

    def _stop_runtime(self):
        """停止本轮（与 main_window._stop_run 同路径：mission_controller.stop）。"""
        try:
            mw = self.window()
            mc = getattr(mw, "mission_controller", None) if mw is not None else None
            if mc is not None and hasattr(mc, "stop"):
                mc.stop()
                return True
        except Exception:
            logging.getLogger("gui.command_deck").exception("停止任务失败")
        return self._invoke_runtime("stop")

    def _on_pause(self):
        """暂停/继续（runtime 状态机 paused ⇄ running）。

        runtime 的暂停 = 状态机置 paused（orchestrator 的实际挂起由
        EmergencyMonitor 判定）；继续时若已无运行线程，_resume_runtime 会把
        状态落回 stopped，避免 api 状态卡在 running 使「开始任务」被静默吞掉。
        """
        if self._user_paused:
            ok = self._resume_runtime()
            self._user_paused = False
            self.pause_btn.setText("暂停")
            if ok:
                self.led.setText("● 已继续")
                self.led.setStyleSheet(led_style(ACCENT))
                self.set_run_status("● 已继续")
            else:
                self.set_run_status("● 继续失败：runtime 未就绪（无 api）")
            return
        ok = self._invoke_runtime("pause")
        if ok:
            self._user_paused = True
            self.pause_btn.setText("继续")
            self.led.setText("⏸ 已暂停")
            self.led.setStyleSheet(led_style(WARN))
            self.set_run_status("● 已暂停（点「继续」恢复）")
        else:
            self.set_run_status("● 暂停失败：runtime 未就绪（无 api）")

    def _on_banner_continue(self):
        """人机横幅「继续」。"""
        ok = self._resume_runtime()
        self.banner.clear()
        if ok:
            self.led.setText("● 已继续")
            self.led.setStyleSheet(led_style(ACCENT))
            self.set_run_status("● 已请求继续")
        else:
            self.set_run_status("● 继续失败：runtime 未就绪（无 api）")

    def _on_banner_skip(self):
        """人机横幅「跳过」：runtime 无逐目标跳过接口 → 结束本轮（跳过剩余目标）。"""
        self.banner.clear()
        self._stop_runtime()
        self.led.setText("● 已跳过剩余目标")
        self.led.setStyleSheet(led_style(WARN))
        self.set_run_status("● 已跳过剩余目标（本轮结束）")

    # 录制（世界图迁入；F10 → stop_recording）

    def _rec_hud_append(self, line):
        """录制相关 HUD 输出（存在才发）。"""
        if self._rec_hud is not None:
            try:
                self._rec_hud.append_external(line)
            except Exception:
                pass

    def _force_hud_cleanup(self):
        """强制回收录制 HUD（F10/停止路径兜底——防卡桌面不消失）。"""
        hud = getattr(self, "_rec_hud", None)
        if hud is not None:
            try:
                hud.hide()
            except Exception:
                pass
            if getattr(self, "_rec_hud_own", False):
                try:
                    hud.destroy()
                    hud.deleteLater()
                except Exception:
                    pass
            self._rec_hud = None
        self._rec_hud_own = False
        if getattr(self, "_countdown_timer", None) is not None:
            try:
                self._countdown_timer.stop()
            except Exception:
                pass
            self._countdown_timer = None
        # 保险丝：1s 后复查 overlay 是否仍可见——仍可见强制隐藏
        try:
            def _recheck():
                try:
                    if hud is not None:
                        try:
                            if hud.overlay.isVisible():
                                hud.overlay.hide()
                        except Exception:
                            pass
                except Exception:
                    pass

            QTimer.singleShot(1000, _recheck)
        except Exception:
            pass

    def _toggle_record(self):
        """录制/停止轨迹（玩家手动操作记录——回放复现用）。

        HUD 联动：开始→显眼提示+3 秒倒计时，每个操作实时显示；
        停止→完成信息 + HUD 收起。F10 也可停止（main_window 接入
        stop_recording()）。
        """
        if self._recorder is not None and self._recorder.recording:
            events = self._recorder.stop()
            path = self._recorder.save()
            self._recorder = None
            if self._countdown_timer is not None:
                self._countdown_timer.stop()
                self._countdown_timer = None
            self.record_btn.setText("● 录制轨迹")
            self.record_btn.setStyleSheet("")
            if path:
                self.set_run_status(f"● 录制完成：{len(events)} 事件 → {path.name}")
                self._rec_hud_append(f"[录制] 完成：{len(events)} 事件 → {path.name}")
                # 同步自定义地图（轨迹立即在本页目标树平级展示）
                try:
                    from knowledge.guides_loader import sync_custom_map, \
                        custom_enabled_names
                    # 只新增自己的轨迹，不动用户勾选状态
                    n = sync_custom_map(custom_enabled_names() | {path.name})
                    self.set_run_status(
                        f"● 录制完成：{len(events)} 事件 → {path.name}"
                        f"（自定义地图已同步 {n} 条）")
                    mw = self.window()
                    if mw is not None and hasattr(mw, "refresh_command_deck"):
                        mw.refresh_command_deck()
                except Exception:
                    logging.getLogger("gui.command_deck").exception(
                        "录制后自定义地图同步失败")
            else:
                self.set_run_status("● 录制结束：无事件（未录制到操作）")
                self._rec_hud_append("[录制] 结束：无事件")
            # 录制结束收起 HUD（统一走 _force_hud_cleanup——含自建回收+倒计时停）
            self._force_hud_cleanup()
            return
        # 开始录制
        from runtime.input.recorder import TrajectoryRecorder
        from runtime.drivers.local.window import find_game_window
        game = find_game_window()
        hwnd = game["hwnd"] if game else None
        if hwnd:
            try:
                from runtime.win_capture import set_foreground_with_retry
                set_foreground_with_retry(hwnd)
            except Exception as e:
                logging.getLogger("gui.command_deck").warning(
                    "录制前置顶失败 hwnd=%s: %s", hwnd, e)
        # 录制灵敏度接线（从设置读取，默认 4，校验 1-5）
        rec_sens = 4
        try:
            from config.settings import get as _cfg_get
            _v = _cfg_get("REPLAY_SENSITIVITY", "4")
            if _v:
                f = float(_v)
                if 1 <= f <= 5:
                    rec_sens = int(f)
        except Exception as e:
            # 静默 → 灵敏度读不到时用户无从得知；此处补日志（默认 4 仍是安全兜底）
            logging.getLogger("gui.command_deck").warning(
                "录制灵敏度读取失败（沿用默认 4）: %s: %s", type(e).__name__, e)
        self._recorder = TrajectoryRecorder(game_hwnd=hwnd,
                                            game_sensitivity=rec_sens)
        # HUD 实时显示当前操作（游戏窗口存在则跟随窗口；不存在则自建独立 HUD）
        hud = None
        self._rec_hud_own = False
        try:
            mw = self.window()
            if mw is not None and hasattr(mw, "ensure_hud"):
                hud = mw.ensure_hud()
            if hud is None and mw is not None:
                from gui.overlay import GameHudController
                bus = getattr(mw, "event_bus", None)
                if bus is not None:
                    hud = GameHudController(bus, None)
                    hud.show()
                    self._rec_hud_own = True
        except Exception:
            hud = None
        self._rec_hud = hud
        if hud is not None:
            try:
                hud.show()  # 幂等：确保 overlay 可见 + 重新定位
            except Exception:
                pass

            def _hook(ev):
                if ev["type"] == "key":
                    line = f"[录制] {ev['key'].upper()} 按住 {ev['duration']:.1f}s"
                elif ev["type"] == "view":
                    line = f"[录制] 视角 ({ev['dx']:+.3f}, {ev['dy']:+.3f})"
                elif ev["type"] == "click":
                    line = f"[录制] 点击 ({ev['nx']:.2f}, {ev['ny']:.2f})"
                else:
                    line = f"[录制] {ev.get('text', '')}"
                hud.append_external(line)

            self._recorder.set_event_hook(_hook)
            # 显眼开始提示 + 3 秒倒计时（主线程 QTimer 每秒一条）
            self._rec_hud_append("▶▶▶ 录制开始 —— 3 秒后正式记录 ◀◀◀")
            self._countdown_timer = QTimer(self)
            self._countdown_timer.setInterval(1000)
            counts = iter(["3…", "2…", "1…", "开始记录！"])

            def _tick():
                try:
                    self._rec_hud_append(f"[录制] {next(counts)}")
                except StopIteration:
                    self._countdown_timer.stop()
                    self._countdown_timer = None

            self._countdown_timer.timeout.connect(_tick)
            self._countdown_timer.start()
        if not self._recorder.start():
            self.set_run_status("● 录制启动失败")
            # 启动失败兜底：HUD/倒计时定时器/_recorder 必须回收
            self._force_hud_cleanup()
            self._recorder = None
            return
        self.record_btn.setText("■ 停止录制")
        # U-06: 录制中=危险/录制语义，统一到 DANGER
        self.record_btn.setStyleSheet(f"color: {DANGER}; font-weight: 700;")
        # 明显状态提示（契约第 7 条：沿用 LED 或 run_status——此处用 run_status，
        # LED 会被 5s 健康刷新覆盖）
        self.set_run_status("● 录制中 —— 3 秒后正式记录（请切到游戏窗口操作）"
                            + ("，操作实时显示在 HUD" if hud is not None else ""))

    # R12 公共 API（main_window 不再 reach-in 私有）

    def is_recording(self):
        """状态查询（main_window 判录制态）。"""
        return (self._recorder is not None
                and getattr(self._recorder, "recording", False))

    def stop_recording(self):
        """公共停止 API（T4 把 F10 转调本方法）。

        已停止或无活动录制时静默 no-op（不得抛异常）。
        """
        if self.is_recording():
            try:
                self._toggle_record()
            except Exception:
                logging.getLogger("gui.command_deck").exception(
                    "F10 停止录制失败")
        # 兜底：无论如何强制清理 HUD（防卡桌面）
        try:
            self._force_hud_cleanup()
        except Exception:
            pass

    # 运行时事件

    def _track_event(self, event):
        """观察中心两项能力等价承接：① 事件类型计数统计 ② 时间线追加（上限 50）。"""
        et = getattr(event, "type", "") or "?"
        self._counts[et] = self._counts.get(et, 0) + 1
        total = sum(self._counts.values())
        top = "、".join(f"{k}×{v}" for k, v in
                        sorted(self._counts.items(), key=lambda x: -x[1])[:5])
        self._stats.setText(f"已接收 {total} 事件 | {top}")
        ctx = getattr(event, "context", None)
        ctx = ctx if isinstance(ctx, dict) else {}
        detail = getattr(event, "detail", "") or ""
        if not isinstance(detail, str):
            detail = str(detail)
        status_val = ctx.get("status") if isinstance(ctx, dict) else None
        suffix = f" ({status_val})" if status_val else ""
        item = QListWidgetItem(f"[{et}] {detail}{suffix}")
        item.setForeground(_timeline_color(et))
        self.timeline.addItem(item)
        while self.timeline.count() > self.timeline_panel.max_items():
            self.timeline.takeItem(0)
        self.timeline.scrollToBottom()

    def on_event(self, event):
        # 统计/时间线先记（未识别事件同样入统计与时间线——观察中心行为等价）
        try:
            self._track_event(event)
        except Exception:
            logging.getLogger("gui.command_deck").exception(
                "事件统计/时间线更新失败")
        # 韧性：残缺事件（type 缺失 / 属性读取抛错 / 未覆盖分支）→ 走
        # _dispatch_event 内部 try/except 兜底 + 日志；页面继续存活。
        try:
            self._dispatch_event(event)
        except Exception as e:
            logging.getLogger("gui.command_deck").exception(
                "on_event 分派失败（页面继续存活）: %s: %s",
                type(e).__name__, e)

    def _dispatch_event(self, event):
        """事件分派核心——残缺事件不抛异常（供 on_event try/except 兜底）。"""
        etype = getattr(event, "type", None) or "unknown"
        if etype == "run_started":
            self._run_no += 1
            self.led.setText("● 运行中")
            self.set_run_status("● 正在搜索宝箱", busy=True)
            self.led.setStyleSheet(led_style(ACCENT))
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.banner.clear()
        elif etype == "run_finished":
            # U-33: context None 防护（之前 event.context.get(...) 在 context 缺失时崩溃）
            # 韧性：event.context 无属性 → getattr 兜底
            ctx = getattr(event, "context", None)
            ctx = ctx if isinstance(ctx, dict) else {}
            result = ctx.get("result", "")
            if result == "gate_blocked":
                # G3 门槛拦截——显示可行动原因（中文）
                reasons = ctx.get("reasons") or []
                fails = ctx.get("fails") or []
                msg = "执行被拦截（G3 能力门槛）"
                if reasons:
                    msg += "：\n" + "\n".join(str(r) for r in reasons[:4])
                elif fails:
                    msg += "：" + "、".join(str(f) for f in fails[:4])
                self.led.setText("● " + msg[:100])
                self.led.setStyleSheet(led_style(WARN))
                self.set_run_status("● 执行被拦截（环境不满足）")
            elif result in ("invalid", "crashed"):
                err = ctx.get("error") or ctx.get("fails")
                self.led.setText("● 失败：" + result + (f" ({err})" if err else ""))
                self.led.setStyleSheet(led_style(DANGER))
                self.set_run_status("● 执行失败")
                # BUG-013：crashed/invalid 时任务队列复位（防 UI 停留"运行中"）
                for leaf in self.rows.values():
                    leaf.setText(TREE_COL_STATUS, STATUS_TEXT.get("pending", "待命"))
                    leaf.setForeground(TREE_COL_STATUS, _status_color("pending"))
            elif result == "stopped":
                # 审查：被停止不能显示"完成"（误导）
                self.led.setText("● 已停止")
                # U-06: 统一到 WARN（"停止"是过程态非错误，与"停止中"同色）
                self.led.setStyleSheet(led_style(WARN))
                self.set_run_status("● 已停止")
            elif result == "no_targets":
                # 审查：空目标不能显示"完成"
                self.led.setText("● 无目标可执行")
                self.led.setStyleSheet(led_style(WARN))
                self.set_run_status("● 无目标")
            elif result == "some_failed":
                # 审查：部分失败不显示"完成"
                self.led.setText("● 部分完成（有失败目标）")
                self.led.setStyleSheet(led_style(WARN))
                self.set_run_status("● 部分完成")
            else:
                self.led.setText("● 空闲")
                self.led.setStyleSheet(led_style(TEXT_MUTED))
                self.set_run_status("● 完成")
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.banner.clear()
            # 本轮完成的点位下次重建即灰显（chest_done）
            self._refresh_done_cache()
        elif etype == "state_changed":
            self._last_state = event.to_state
            # 启动进度反馈（ready:checking:N/6 → 界面归一化进度）
            # 韧性：event.detail 可能不是 str（None / dict）→ 先 isinstance 保护
            d = event.detail or ""
            if isinstance(d, str) and d.startswith("ready:checking:"):
                self.set_run_status(
                    f"● 准备游戏画面（{d.split(':')[2]}）…", busy=True)
        elif etype == "target_progress":
            # 韧性：context 缺失 / None / 非 dict → {} 兜底，不抛 AttributeError
            ctx = getattr(event, "context", None)
            ctx = ctx if isinstance(ctx, dict) else {}
            status = ctx.get("status")
            if ctx.get("target") in self.rows and status:
                leaf = self.rows[ctx["target"]]
                leaf.setText(TREE_COL_STATUS, STATUS_TEXT.get(status, status))
                leaf.setForeground(TREE_COL_STATUS, _status_color(status))
            if status == "failed":
                self.inspector.on_failure(
                    run_no=self._run_no, state=self._last_state,
                    observation=self.snapshot.text(),
                    action="—", input_info="—",
                    reason=ctx.get("reason") or "目标失败",
                    category=category_text(ctx.get("category")))
        elif etype == "fail_recorded":
            # 失败事件直达检查器（修复：orchestrator 发 fail_recorded，
            # 原只监听 target_progress/action_executed——检查器永远收不到）
            ctx = event.context or {}
            cat = category_text(ctx.get("category")) \
                or (event.detail or "").split(":")[0]
            self.inspector.on_failure(
                run_no=self._run_no, state=self._last_state,
                observation=self.snapshot.text(),
                action=event.detail or "—",
                input_info=f"target={ctx.get('target') or '—'}",
                reason=ctx.get("error") or event.detail or "未知失败",
                category=cat)
        elif etype == "observation":
            # 韧性：context 缺失 / None / 非 dict → {} 兜底
            ctx = getattr(event, "context", None)
            ctx = ctx if isinstance(ctx, dict) else {}
            self.snapshot.update_snapshot(
                ctx.get("observer"), getattr(event, "detail", "") or ctx.get("target"),
                ctx.get("confidence"), ctx.get("room"))
        elif etype == "action_executed":
            # 韧性：context 缺失 / None / 非 dict → {} 兜底
            ctx = getattr(event, "context", None)
            ctx = ctx if isinstance(ctx, dict) else {}
            if ctx.get("success") is False:
                reason = ctx.get("error") or "unknown"
                suggested = ("重启并以管理员身份运行" if "uipi" in reason.lower()
                             else "检查游戏窗口是否在前台")
                self.inspector.on_failure(
                    run_no=self._run_no, state=self._last_state,
                    observation=self.snapshot.text(),
                    action=event.detail, input_info=f"{ctx.get('backend')} ✗",
                    reason=f"{reason} → 建议: {suggested}",
                    category="F1 输入失败")
        elif etype == "pause_requested":
            # 人机横幅：暂停原因 + 继续/跳过（P1 上半区第三条）
            ctx = event.context or {}
            self.led.setText("⏸ 已暂停")
            self.led.setStyleSheet(led_style(WARN))
            self.banner.show_reason(
                f"暂停原因：{ctx.get('reason') or '未提供'}"
                + (f"（{ctx.get('detail')}）" if ctx.get("detail") else ""))
        elif etype == "human_intervention":
            # 韧性：context 缺失 / None / 非 dict → {} 兜底
            ctx = getattr(event, "context", None)
            ctx = ctx if isinstance(ctx, dict) else {}
            self.led.setText("⚠ 人工介入")
            self.led.setStyleSheet(led_style(WARN))
            self.banner.show_reason(
                f"人工介入：{ctx.get('reason')}"
                + (f" {ctx.get('detail', '')}" if ctx.get("detail") else ""))
            self.inspector.on_failure(
                run_no=self._run_no, state=self._last_state,
                observation=self.snapshot.text(),
                action="—", input_info="—",
                reason=f"人工介入：{ctx.get('reason')} {ctx.get('detail', '')}",
                category="EMERGENCY")
        else:
            logging.getLogger("gui.command_deck").warning(
                "未处理事件: %s", etype)

    def reset(self):
        for leaf in self.rows.values():
            if leaf.checkState(TREE_COL_TARGET) == Qt.Checked:
                leaf.setText(TREE_COL_STATUS, "待命")
                leaf.setForeground(TREE_COL_STATUS, _status_color("pending"))
        self.led.setText("● 空闲")
        self.led.setStyleSheet(led_style(TEXT_MUTED))
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._user_paused = False
        self.pause_btn.setText("暂停")
        self.banner.clear()
        self.snapshot.update_snapshot("—", "—", None, "")
        self.inspector.reset()
