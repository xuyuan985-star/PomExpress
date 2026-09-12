from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication, QLabel

from pathlib import Path
from config.settings import data_path, knowledge_root

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录

# U-10: 所有 import 集中在文件顶部——避免历史合并残留在类之间夹 import。
# T4（三页收口）：6 页导航收成 3 页——观察中心并入「出击」(CommandDeck)、
# 知识体系并入「藏宝图」(WorldGraphPage)、任务中心降级为出击页内嵌折叠卡
# （经 deck.task_center / is_running / stop_task / shutdown 直通）。
# gui/pages/{placeholder,observation,knowledge}.py 已删除——不再有 import。
from gui.pages.command_deck import CommandDeck
from gui.pages.settings import SettingsPage
from gui.pages.world_graph import WorldGraphPage
from gui.controllers.mission_controller import MissionController
from gui.safe import gui_safe
from gui.sound import on_runtime_event as _on_runtime_sound
from qfluentwidgets import (FluentIcon, FluentWindow, NavigationItemPosition)
from gui.theme import apply_theme, ACCENT, DANGER, WARN

# D6（S2-B）：LED 字号/样式统一收编到 command_deck.led_style()——不再在
# main_window 重复定义 LED_FONT_SIZE / f-string。保留别名供本地旧调用使用。
from gui.pages.command_deck import led_style as _led_style  # noqa: E402
from config.settings import QSETTINGS_APP, QSETTINGS_ORG

# LED 字号统一（U-05 + U-06 共用）——避免每处重复 "12px"。
# D6 后续：逐步淘汰本模块 LED_FONT_SIZE，全部走 _led_style(token)。
LED_FONT_SIZE = "12px"


class HealthWorker(QThread):
    """模块级 HealthWorker（可测试/可复用——不再嵌套在 __init__ 内）。

    审查根因：本环境 PySide6 QThread 跨线程信号 → QObject 槽不投递
    （实测 dict/str/QPixmap 均失效）——结果改写入 self._result，
    由主线程定时轮询消费（见 MainWindow._poll_health）。
    T4 死代码清理：原 `done = Signal(dict, str)` 全仓从未 connect / 从未
    emit（结果一律经 _result 轮询），已删除。
    """

    def run(self):
        try:
            from runtime.health import check_health
            result = check_health()
            # health 结果必须 dict（None/False → 明确错误，不静默卡界面）
            if not isinstance(result, dict):
                raise RuntimeError(f"check_health 返回非法类型: {type(result).__name__}")
            self._result = (result.get("capability", {}), "")
        except Exception:
            # 完整 traceback 回传（GUI 可定位，非只 str(e)）
            import traceback
            self._result = ({}, traceback.format_exc())


class MainWindow(FluentWindow):
    # T4 死代码清理：原 `event_received = Signal(object)`（注释称保留给外部/
    # 测试兼容）经全仓 grep 确认从未 emit / 从未 connect——事件一律走
    # _runtime_queue 轮询，故删除该信号声明。

    def __init__(self, targets, event_bus, api, parent=None,
                 mission_controller=None):
        super().__init__(parent)
        # event_bus 必须非空（None 时所有订阅/事件驱动会崩）
        if event_bus is None:
            raise ValueError("MainWindow 需要 EventBus（测试请传 Fake 总线）")
        apply_theme(QApplication.instance())
        # 版本信息入标题（用户反馈可定位构建）——单一版本源
        from config.version import APP_VERSION
        self.setWindowTitle(f"帕姆巡宝 v{APP_VERSION}")
        # 最小尺寸按屏幕可用区动态限制（小屏/DPI 缩放不溢出）
        try:
            from PySide6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                self.setMinimumSize(
                    min(1180, avail.width()), min(720, avail.height()))
            else:
                self.setMinimumSize(1180, 720)
        except Exception:
            self.setMinimumSize(1180, 720)
        # 业务封装注入（缺省内部构造，测试可传 Fake）
        # 知识目录显式注入——与 run.py 目标加载同源（真点位执行包，
        # 内含 30 条真点位模板 workflow；guides/maps 是展示库，无 workflow 不能执行）
        # is not None 判定（falsy 的 FakeController 不被误替换）
        self.mission_controller = mission_controller \
            if mission_controller is not None else MissionController(
                api, knowledge_dir=str(knowledge_root() / "source" / "black_tower_test"))
        # DPI 修复：窗口几何保存/恢复（游戏切分辨率时不被放大/移出屏幕）
        self._user_geometry = None
        self._screen_dpi = None
        self._restore_geometry()
        self._watch_screen_changes()
        # 目标数据存档（录制同步后指挥台刷新用）
        self._all_targets = list(targets or [])

        # 指挥台构造兜底：失败走 error_page（不再二次创建失败对象——）
        self.command_deck = self._safe_page(CommandDeck, targets or [])
        # 页面构造异常隔离——单页失败不拖垮主窗口
        self.world_graph = self._safe_page(WorldGraphPage)
        # 攻略体系"执行此区域"→ 知识包匹配 → 指挥台执行（接线）
        try:
            view = self.world_graph._view if hasattr(
                self.world_graph, "_view") else None
            if view is not None and hasattr(view, "run_requested"):
                view.run_requested.connect(self._on_guide_run)
        except Exception as e:
            logging.getLogger("gui.main_window").debug(
                "guide_view.run_requested.connect 失败: %s", e)
        # T4（三页收口）：观察中心 / 知识体系 / 任务中心 不再作为独立页面存在——
        # · observation → 并入「出击」(CommandDeck)：实时观测卡片 + 事件统计/时间线
        # · knowledge → 并入「藏宝图」(WorldGraphPage) 顶部统计条
        # · task_center → 降级为出击页内嵌折叠卡（deck.task_center）
        # 故不再保留 observation / knowledge / studio 三个属性；
        # 原 self.studio 承载的三条语义改走 deck 直通（见 _emergency_hotkey /
        # closeEvent），语义等价且构造失败时逐处 hasattr 兜底不抛异常。
        self.settings = self._safe_page(SettingsPage)

        for page, name in [
            (self.command_deck, "commandDeck"),
            (self.world_graph, "worldGraph"),
            (self.settings, "settings"),
        ]:
            page.setObjectName(name)

        # T4（三页收口）：导航恰好 3 项——出击（吃观察中心）/ 藏宝图（吃知识体系）
        # / 作坊（设置大扩）。标题与蓝图一致；任务中心不再占导航位。
        for page, icon, nav_name, pos in [
            (self.command_deck, FluentIcon.ROBOT, "出击",
             NavigationItemPosition.TOP),
            (self.world_graph, FluentIcon.GLOBE, "藏宝图",
             NavigationItemPosition.TOP),
            (self.settings, FluentIcon.SETTING, "作坊",
             NavigationItemPosition.BOTTOM),
        ]:
            item = self.addSubInterface(page, icon, nav_name, position=pos)
            try:
                item.setToolTip(nav_name)
            except Exception as e:
                import logging
                logging.getLogger("gui.main_window").debug(
                    "导航项 setToolTip 失败: %s", e)

        # U-19：导航展开宽度 160（≥150 推荐值）——保证文字标签始终可见，
        # 新用户无需悬停 tooltip 即可识别 3 个页面；setCollapsible(False)
        # 禁止折叠回 56px 图标条模式。
        self.navigationInterface.setExpandWidth(160)
        self.navigationInterface.setCollapsible(False)

        title_bar = self.titleBar
        # 标题栏主副两段：品牌名 + 版本号。窗口标题（任务栏/Alt-Tab 可见）另由
        # setWindowTitle 设置，含完整项目名与版本。
        from config.version import APP_VERSION
        brand = QLabel("帕姆巡宝")
        brand.setObjectName("brandLabel")
        sub = QLabel(f"v{APP_VERSION}")
        sub.setObjectName("brandSubLabel")
        title_bar.hBoxLayout.insertWidget(0, brand)
        title_bar.hBoxLayout.insertWidget(1, sub)

        self.event_bus = event_bus
        self.api = api
        self.event_bus.subscribe(self._on_runtime_event)
        # 主事件链轮询（本环境跨线程 Qt 信号不可靠）——runner 线程只入队，
        # 主线程 QTimer 消费（见 _poll_runtime_events）。deque：append/popleft
        # 原子（审查：list 整体替换在并发下丢事件）
        from collections import deque as _deque
        self._runtime_queue = _deque()
        self._event_poll = QTimer(self)
        self._event_poll.setInterval(50)
        self._event_poll.timeout.connect(self._poll_runtime_events)
        self._event_poll.start()
        _deck = self._deck()
        if _deck is not None:
            _deck.run_requested.connect(
                lambda targets: self._start_run(targets))
            _deck.stop_requested.connect(self._stop_run)

        self._health_worker = HealthWorker(self)
        # 退出保护注册：HealthWorker 也是 QThread——aboutToQuit 时若仍在跑
        # （check_health 含 OCR 模型加载 1-2s），Qt 析构必崩。创建即注册，
        # guard 只查注册表（topLevelWidgets 遍历在窗口销毁中不可靠）。
        try:
            from gui.thread_guard import register_qthread, unregister_qthread
            register_qthread(self._health_worker)
            self._health_worker.finished.connect(
                lambda: unregister_qthread(self._health_worker))
        except Exception as e:
            logging.getLogger("gui.main_window").debug(
                "health_worker.finished.connect 失败: %s", e)
        if _deck is not None:
            _deck.set_health_status("正在检测环境...", busy=True)
        # 审查根因：本环境 QThread 信号 → QObject 槽不投递——结果经
        # self._result 属性 + 主线程轮询消费（_poll_health）
        self._health_worker.start()
        # 结果消费轮询（500ms）+ 系统状态周期刷新（5s 重新检测）
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll_health)
        self._poll_timer.start()
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(5000)
        self._health_timer.timeout.connect(self._refresh_health)
        self._health_timer.start()
        # 监听 IPC 唤醒（第二实例激活本窗口）
        # BUG-010：不无条件 removeServer（会删掉并发实例刚建的 server）——
        # 仅 listen 失败（残留）时才清理重试一次
        self._wake_server = None
        try:
            from PySide6.QtNetwork import QLocalServer
            server = QLocalServer(self)
            if not server.listen("PomExpress_Wake"):
                QLocalServer.removeServer("PomExpress_Wake")
                if not server.listen("PomExpress_Wake"):
                    raise RuntimeError("QLocalServer listen 失败（二次）")
            server.newConnection.connect(self._on_wake_request)
            self._wake_server = server
        except Exception:
            # P0-001：IPC 唤醒监听失败不静默（可选功能，但需可查）
            import logging
            logging.getLogger("gui.main_window").exception(
                "IPC 唤醒监听启动失败")
            self._wake_server = None

    def _on_wake_request(self):
        # 收到第二实例 activate 消息 → 置顶显示
        # IPC 消息格式校验——只接受精确 "activate"（防非法/损坏消息）
        try:
            conn = self._wake_server.nextPendingConnection()
            if conn is None:
                return
            raw = bytes(conn.readAll()).decode("utf-8", errors="ignore")
            conn.disconnectFromServer()
            if raw.strip() != "activate":
                import logging
                logging.getLogger("gui.main_window").warning(
                    "非法 IPC 消息: %r", raw[:60])
                return
            self.show()
            self.raise_()
            self.activateWindow()
        except Exception:
            # P0-001：唤醒处理异常不静默
            import logging
            logging.getLogger("gui.main_window").exception(
                "IPC 唤醒处理失败")

    def start_foreground_watch(self, interval_ms=3000):
        """持续前台守护：定时检测游戏窗口是否在前台，不在则自动拉置顶。

        任务运行期间启用；任务失败/停止/结束时必须 stop_foreground_watch
        停掉——否则失败后仍疯狂抢前台（0.6.0 排查修复）。
        """
        if getattr(self, "_fg_watch", None) is not None:
            return
        self._fg_watch = QTimer(self)
        self._fg_watch.setInterval(interval_ms)
        self._fg_watch.timeout.connect(self._ensure_game_foreground)
        self._fg_watch.start()
        # 同时开启 HUD（对齐 M7：游戏窗口左下角日志层）
        self._start_hud()

    def stop_foreground_watch(self):
        """停止前台守护（任务失败/停止/结束时调用——防失败后疯狂拉置顶）。"""
        fw = getattr(self, "_fg_watch", None)
        if fw is not None:
            try:
                fw.stop()
            except Exception as e:
                logging.getLogger("gui.main_window").warning(
                    "foreground_watch.stop() 失败: %s", e)
            self._fg_watch = None

    def ensure_hud(self):
        """HUD 控制器（无则创建）——任务中心子进程任务复用同一 HUD。

        返回 GameHudController 或 None（游戏窗口不存在时）。"""
        try:
            if getattr(self, "_hud", None) is not None:
                return self._hud
            from runtime.drivers.local.window import find_game_window
            game = find_game_window()
            if game is None:
                return None
            from gui.hotkey import GlobalHotkey
            if getattr(self, "_hotkeys", None) is None:
                self._hotkeys = GlobalHotkey(self)
                self._hotkeys.pressed.connect(self._on_hotkey)
                self._hotkeys.register("f10", None)
            from gui.overlay import GameHudController
            self._hud = GameHudController(self.event_bus, game["hwnd"])
            self._hud.show()
            return self._hud
        except Exception:
            import logging
            logging.getLogger("gui.main_window").exception("ensure_hud 失败")
            return None

    def _start_hud(self):
        """F10 全局热键 + 游戏窗口 HUD 日志层（对齐 M7）。
        U-03（u6 终审收尾）：HUD 已存在且存活时复用——避免每轮任务泄漏
        EventBus 订阅 + 200ms QTimer + overlay 控件。委托 ensure_hud 即可
        单写者完成（HUD 创建/热键注册逻辑仅一处）。"""
        try:
            self.ensure_hud()
        except Exception:
            import logging
            logging.getLogger("gui.main_window").exception("HUD/热键启动失败")

    def _on_hotkey(self, key):
        # keyboard 回调线程 → 信号 → 主线程
        if key == "f10":
            self._emergency_hotkey()

    def _emergency_hotkey(self):
        """F10：紧急停止——释放全部按键 + 停止任务 + 停止录制 + HUD 提示。"""
        import logging
        logging.getLogger("gui.main_window").warning("F10 紧急停止触发")
        try:
            # 释放可能卡住的按键
            mc = self.mission_controller
            if mc is not None:
                mc.stop()
        except Exception as e:
            logging.getLogger("gui.main_window").warning(
                "F10: mission_controller.stop() 失败: %s", e)
        try:
            # T4：录制控件已从世界图迁到「出击」页（CommandDeck）——F10 停录制
            # 改走 deck.stop_recording()（T1 公共 API：非录制期静默 no-op）。
            # R12（S2-B）语义不变：公共 API is_recording()/stop_recording()——
            # 不再 reach-in 私有 `_recorder` / `_toggle_record`。
            deck_rec = self._deck()
            if deck_rec is not None and hasattr(deck_rec, "stop_recording"):
                deck_rec.stop_recording()
        except Exception as e:
            logging.getLogger("gui.main_window").warning(
                "F10: deck.stop_recording() 失败: %s", e)
        try:
            import ctypes
            # 兜底：释放 W/A/S/D/Esc/空格/Shift 的 keyup
            # F1 修复 ：原列表漏 Shift——若 executor.emergency_stop 失败、
            # 走到此处兜底，Shift 会继续按住 → 游戏保持 shift+run 加速状态。
            # 0xA0=LSHIFT, 0xA1=RSHIFT，与 executor._step_executor_core.py:113
            # 的 release_key("shift") 对齐。
            for vk in (0x57, 0x41, 0x53, 0x44, 0x1B, 0x20, 0xA0, 0xA1):
                ctypes.windll.user32.keybd_event(vk, 0, 0x0002, 0)
            # 兜底：释放鼠标左/右键（长按中 F10 也必须松开）
            ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTBUTTONUP
            ctypes.windll.user32.mouse_event(0x0010, 0, 0, 0, 0)  # RIGHTBUTTONUP
        except Exception as e:
            logging.getLogger("gui.main_window").warning(
                "F10: 兜底按键/鼠标释放失败: %s", e)
        if getattr(self, "_hud", None) is not None:
            self._hud.overlay.set_emergency()
        deck = self._deck()
        if deck is not None:
            deck.led.setText("⚠ F10 紧急停止（按键已释放）")
            # U-06: 统一到 DANGER——F10 紧急停止 = 危险反馈，红色与 DANGER 同源
            # （之前 #E64545 与 DANGER #FF6B6B 视觉接近但属不同字面量）。
            # D6（S2-B）：走 _led_style(token) 单一入口——消除 #E64545 vs DANGER
            # #FF6B6B 双源漂移；保持 LED_FONT_SIZE 一致（command_deck 默认 12px）。
            deck.led.setStyleSheet(_led_style(DANGER))

    def _poll_health(self):
        """轮询消费 HealthWorker 结果（本环境 QThread 信号→QObject 槽不投递）。"""
        w = self._health_worker
        result = getattr(w, "_result", None)
        if result is None:
            # 窗口自愈（排查窗口消失）：非关闭流程中主窗口不可见 →
            # 500ms 内恢复显示。凶手是 hide/系统误关闭时直接自愈；
            # 对象已销毁则 show 抛错 → destroyed guard 已 os._exit 兜底。
            # 最小化窗口不触发自愈（isVisible 对最小化
            # 也返回 False——否则用户最小化后 500ms 被强制恢复）
            if not self.isVisible() \
                    and not self.isMinimized() \
                    and not getattr(self, "_we_closing", False):
                try:
                    import time as _time
                    p = self._trace_log_path()
                    p.parent.mkdir(parents=True, exist_ok=True)
                    with open(p, "a", encoding="utf-8") as f:
                        f.write(f"{_time.strftime('%Y-%m-%d %H:%M:%S')} "
                                f"self-heal: MainWindow 不可见 → show() 恢复\n")
                    self.show()
                except Exception as e:
                    logging.getLogger("gui.main_window").debug(
                        "self-heal show() 失败: %s", e)
            return
        w._result = None  # 消费
        self._on_health_done(*result)

    def _refresh_health(self):
        """系统状态周期刷新：检测进行中跳过（防重入），完成后经轮询更新。"""
        if self._health_worker.isRunning():
            return
        self._health_worker.start()

    def _ensure_game_foreground(self):
        """检测游戏前台——不在则自动激活（任务运行期间才拉置顶）。

        修复（0.6.0 排查）：任务不在运行状态（失败/停止/空闲）直接返回——
        否则定时器残留会疯狂抢前台。
        """
        try:
            # 运行状态保护：仅 running 期间拉置顶
            mc = getattr(self, "mission_controller", None)
            if mc is not None:
                st = getattr(mc.state, "value", mc.state)
                if st != "running":
                    return
            import ctypes
            from runtime.drivers.local.window import find_game_window
            game = find_game_window()
            if game is None:
                return  # 游戏没开——不打扰
            # HUD 补启：_start_hud 在窗口缺失时会直接跳过——游戏被自动拉起
            # 后此处补启（否则"开始任务后 HUD 不存在"）
            if getattr(self, "_hud", None) is None:
                self._start_hud()
                return
            fg = ctypes.windll.user32.GetForegroundWindow()
            if fg != game["hwnd"]:
                from runtime.win_capture import set_foreground_with_retry
                set_foreground_with_retry(game["hwnd"])
            # HUD 跟随窗口位置
            hud = getattr(self, "_hud", None)
            if hud is not None and hud.overlay.isVisible():
                hud.reposition()
        except Exception as e:
            logging.getLogger("gui.main_window").debug(
                "HUD.reposition() 失败: %s", e)

    def _safe_page(self, page_cls, *args):
        """页面构造异常 → ErrorPage（显示错误，主窗口照常启动）。
        只尝试一次——失败显示错误页，不重复创建失败对象。"""
        try:
            return page_cls(*args)
        except Exception as e:
            import traceback
            traceback.print_exc()
            # R2（S2-B）：error_page 已迁入 gui.pages.base_page（与 BasePage 同源）。
            from gui.pages.base_page import error_page
            return error_page(page_cls.__name__, str(e))

    def _deck(self):
        """安全取指挥台（构造失败时是 error_page——无 led/按钮，访问前判空）。"""
        d = getattr(self, "command_deck", None)
        return d if d is not None and hasattr(d, "led") else None

    def _on_health_done(self, health, error):
        # 检测完成状态反馈（失败显示原因，不再静默）
        self._health = health or {}
        deck = self._deck()
        if deck is None:
            return
        if error:
            deck.set_health_status("环境检测失败: " + error[:120], busy=False)
        else:
            deck.set_health(health)
            # 健康提示：关键项失败给可行动原因（非管理员/前台）
            hints = []
            if health.get("admin") is False:
                hints.append("输入被拦（非管理员）→ 请以管理员运行")
            if health.get("foreground") is False and health.get("window"):
                hints.append("游戏窗口不在前台 → 切回游戏窗口")
            if hints:
                deck.set_health_status("；".join(hints), busy=False)
            else:
                deck.set_health_status("环境就绪", busy=False)
            self._sync_runtime_state()

    def _sync_runtime_state(self):
        """状态恢复——GUI 启动/重开时主动同步 runtime（不只等事件）。"""
        deck = self._deck()
        if deck is None:
            return
        st = getattr(self.mission_controller, "state", None)
        # BUG-012：状态可能为 str 或 Enum——统一取 value 比较（类型安全）
        if getattr(st, "value", st) == "running":
            deck.led.setText("● 运行中（恢复同步）")
            # U-06: 统一到 ACCENT（#4FD1C5 本身就是 theme.ACCENT 的字面量）
            # D6（S2-B）：走 _led_style(token) 单一入口。
            deck.led.setStyleSheet(_led_style(ACCENT))
            deck.start_btn.setEnabled(False)
            deck.stop_btn.setEnabled(True)

    def _restore_geometry(self):
        """U-11: 启动时恢复用户上次窗口大小+位置（QSettings）。
        DPI 变化时钳制到可用区——游戏切分辨率后窗口不会被甩到屏外。
        裸 QSettings → 双后端（注册表优先 + ini 回退），
        对齐 gui/pages/settings.py:_SettingsStore 与 gui/sound.py:is_enabled。
        """
        try:
            from PySide6.QtCore import QSettings
            from PySide6.QtGui import QGuiApplication
            s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
            w = int(s.value("win_w", 1280))
            h = int(s.value("win_h", 760))
            # 新增 x/y 键（兼容旧 QSettings 缺省——缺则居中）
            x = s.value("win_x")
            y = s.value("win_y")
            # 注册表不可用时回看 ini——
            # 与 gui/sound.py:is_enabled 读侧逻辑一致。
            if s.status() == QSettings.Status.AccessError:
                try:
                    from config.settings import data_path
                    ini = QSettings(str(data_path("settings") / "gui_settings.ini"),
                                    QSettings.Format.IniFormat)
                    w = int(ini.value("win_w", w))
                    h = int(ini.value("win_h", h))
                    if x is None or y is None:
                        x = ini.value("win_x")
                        y = ini.value("win_y")
                except Exception:
                    import logging
                    logging.getLogger("gui.main_window").debug(
                        "ini 回退读取几何失败（用默认值）")
            # 尺寸也必须钳制到屏幕可用区：只钳位置不够——历史保存的尺寸可能
            # 大于当前屏幕（换显示器 / 改缩放 / 曾在更大屏上拉伸过），
            # 那样窗口底部（含页脚按钮）会被切到屏幕外，用户既看不到也点不到。
            # 下限取最小尺寸（setMinimumSize 的 1180x720），上限取可用区。
            try:
                screen = QGuiApplication.primaryScreen()
                if screen is not None:
                    avail = screen.availableGeometry()
                    min_w, min_h = 1180, 720
                    w = max(min(w, avail.width()), min(min_w, avail.width()))
                    h = max(min(h, avail.height()), min(min_h, avail.height()))
            except Exception:
                pass
            self.resize(w, h)
            if x is not None and y is not None:
                # 钳制到当前主屏可用区——防止历史 x/y 越界（屏被拔/缩放）
                try:
                    screen = QGuiApplication.primaryScreen()
                    if screen is not None:
                        avail = screen.availableGeometry()
                        ix = max(avail.left(),
                                 min(int(x), avail.right() - w))
                        iy = max(avail.top(),
                                 min(int(y), avail.bottom() - h))
                        self.move(ix, iy)
                except Exception:
                    self.move(int(x), int(y))
            else:
                self._center_on_screen(w, h)
        except Exception:
            import logging
            logging.getLogger("gui.main_window").warning("窗口几何恢复失败")

    def showEvent(self, event):  # noqa: N802 — Qt 命名
        """首次显示后把整窗收进屏幕可用区。

        构造期拿不到真实边框尺寸（标题栏/边框），且 Qt 的几何是**逻辑像素**
        （本机 devicePixelRatio=1.25）——与 Win32 的物理像素混算会算错。
        故最终收口放在 show 之后，全程只用 Qt 自己的 API（同一坐标系）：
        尺寸上限取 availableGeometry，位置用 frameGeometry 反推客户端坐标。
        """
        super().showEvent(event)
        if not getattr(self, "_geom_clamped", False):
            self._geom_clamped = True
            self._clamp_into_screen()

    def _clamp_into_screen(self):
        """把窗口整体收进当前屏幕可用区（含标题栏与边框）。失败静默。"""
        try:
            from PySide6.QtGui import QGuiApplication
            screen = self.screen() or QGuiApplication.primaryScreen()
            if screen is None:
                return
            avail = screen.availableGeometry()
            # 尺寸：不超过可用区
            w = min(self.width(), avail.width())
            h = min(self.height(), avail.height())
            if (w, h) != (self.width(), self.height()):
                self.resize(w, h)
            # 位置：frameGeometry 含边框，与 avail 同为逻辑像素
            fg = self.frameGeometry()
            fx = min(max(fg.left(), avail.left()),
                     avail.right() + 1 - fg.width())
            fy = min(max(fg.top(), avail.top()),
                     avail.bottom() + 1 - fg.height())
            # 换算回客户端坐标（frame 与 client 的左上差值）
            self.move(self.x() + (fx - fg.left()), self.y() + (fy - fg.top()))
        except Exception:
            pass

    def _center_on_screen(self, w=None, h=None):
        """居中于屏幕可用区（用给定/当前尺寸计算，不依赖 show 之前的 frame）。"""
        try:
            from PySide6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                return
            avail = screen.availableGeometry()
            w = int(w or self.width())
            h = int(h or self.height())
            self.move(avail.left() + max(0, (avail.width() - w) // 2),
                      avail.top() + max(0, (avail.height() - h) // 2))
        except Exception:
            pass

    def _save_geometry(self):
        """U-11: 退出时保存窗口大小+位置（QSettings）。
        裸 QSettings → 双后端（注册表优先 + ini 回退），
        消除受限环境（沙箱/CI）下窗口几何静默丢失。
        对齐 gui/pages/settings.py:_SettingsStore.setValue 写侧逻辑。
        """
        try:
            from PySide6.QtCore import QSettings
            s = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
            s.setValue("win_w", self.width())
            s.setValue("win_h", self.height())
            s.setValue("win_x", self.x())
            s.setValue("win_y", self.y())
            s.sync()
            # 注册表 AccessError → 自动落 ini
            if s.status() == QSettings.Status.AccessError:
                try:
                    from config.settings import data_path
                    ini = QSettings(str(data_path("settings") / "gui_settings.ini"),
                                    QSettings.Format.IniFormat)
                    ini.setValue("win_w", self.width())
                    ini.setValue("win_h", self.height())
                    ini.setValue("win_x", self.x())
                    ini.setValue("win_y", self.y())
                    ini.sync()
                    if ini.status() != QSettings.Status.NoError:
                        import logging
                        logging.getLogger("gui.main_window").warning(
                            "窗口几何保存：双后端均不可写（注册表=%s, ini=%s）",
                            s.status().name, ini.status().name)
                    else:
                        import logging
                        logging.getLogger("gui.main_window").info(
                            "窗口几何保存：注册表不可写，已落 ini 回退")
                except Exception:
                    import logging
                    logging.getLogger("gui.main_window").warning(
                        "窗口几何保存：ini 回退写入失败")
        except Exception:
            import logging
            logging.getLogger("gui.main_window").warning("窗口几何保存失败")

    def _watch_screen_changes(self):
        """DPI 修复：主屏几何/DPI 变化（游戏切分辨率）→ 恢复用户窗口大小。

        游戏全屏切换会使 Qt 重排布局导致窗口放大/移出屏幕。
        U-11: 同时钳制窗口位置到当前主屏可用区。
        """
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        self._screen_dpi = screen.logicalDotsPerInch()

        def _on_change():
            cur = screen.logicalDotsPerInch()
            changed = self._screen_dpi and abs(cur - self._screen_dpi) > 1
            self._screen_dpi = cur
            if changed:
                # 分辨率/DPI 切换 → 恢复用户窗口大小（钳制到可用区）
                saved = (self.width(), self.height()) if not self._user_geometry \
                    else self._user_geometry
                if saved and saved != (0, 0):
                    self.resize(*saved)
                    self._user_geometry = saved
                # U-11: 位置钳制（避免 DPI 变化后窗口漂出屏幕）
                try:
                    avail = screen.availableGeometry()
                    geo = self.geometry()
                    ix = max(avail.left(),
                             min(geo.x(), avail.right() - geo.width()))
                    iy = max(avail.top(),
                             min(geo.y(), avail.bottom() - geo.height()))
                    if (ix, iy) != (geo.x(), geo.y()):
                        self.move(ix, iy)
                except Exception as e:
                    logging.getLogger("gui.main_window").debug(
                        "self.move() 失败: %s", e)
        try:
            screen.logicalDotsPerInchChanged.connect(_on_change)
            screen.geometryChanged.connect(_on_change)
        except Exception:
            import logging
            logging.getLogger("gui.main_window").warning("屏幕变化监听注册失败")

    def shutdown(self):
        """统一关闭（controller/worker/订阅）。"""
        self._save_geometry()  # DPI 修复：退出保存窗口大小
        try:
            self.mission_controller.stop()
        except Exception:
            import logging
            logging.getLogger("gui.main_window").exception("停止 MissionController 失败")
        # 审查：事件文件句柄关闭（bus.close 原无调用方）
        try:
            if getattr(self, "event_bus", None) is not None:
                self.event_bus.close()
        except Exception as e:
            logging.getLogger("gui.main_window").warning(
                "event_bus.close() 失败: %s", e)
        # 审查：HUD 订阅泄漏——GUI 关闭必须 unsubscribe（bus 强引用
        # 阻止 GameHudController 释放，publish 会持续调已关闭的 _on_event）
        try:
            if getattr(self, "_hud", None) is not None:
                self._hud.destroy()
                self._hud = None
        except Exception as e:
            logging.getLogger("gui.main_window").warning(
                "HUD.destroy() 失败: %s", e)
        # 审查：F10 全局热键钩子泄漏——关闭必须 unhook（keyboard 钩子残留
        # 会持续回调到已销毁对象）
        try:
            if getattr(self, "_hotkeys", None) is not None:
                self._hotkeys.unregister_all()
                self._hotkeys = None
        except Exception as e:
            logging.getLogger("gui.main_window").warning(
                "hotkeys.unregister_all() 失败: %s", e)
        self._save_diag_snapshot()

    def _save_diag_snapshot(self):
        """退出现场保存（运行指标/线程/状态）——复盘"昨晚挂了"。"""
        import json
        import threading
        from pathlib import Path
        try:
            snap = {
                "api_state": getattr(self.mission_controller, "state", None),
                "threads": [t.name for t in threading.enumerate()
                            if t.is_alive() and t is not threading.current_thread()],
                "execution_id": getattr(getattr(self, "api", None),
                                       "execution_id", None),
            }
            log_dir = data_path("logs")
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "gui_snapshot.json").write_text(
                json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            import logging
            logging.getLogger("gui.main_window").warning("退出现场保存失败")

    @staticmethod
    def _trace_log_path():
        """退出探针日志路径（exit_trace.log）。"""
        from pathlib import Path as _P
        return data_path("logs") / "exit_trace.log"

    @staticmethod
    def _trace_close(msg):
        """关闭路径探针——直接文件写（不依赖 logging level）。"""
        try:
            import time as _time
            p = MainWindow._trace_log_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"{_time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        except Exception as e:
            logging.getLogger("gui.main_window").debug(
                "trace log 写入失败: %s", e)

    def closeEvent(self, event):
        # 关闭顺序——先停 worker/取消订阅（杜绝后台线程继续发 GUI 信号），
        # 再停 Runtime（防后台继续点击），最后保存诊断
        # BUG-009：wait 超时（check_health 内部阻塞）→ 明确警告而非静默残留
        # 子进程模式（0.6.0 回滚）：m7 任务在独立 QProcess，kill 即停——
        # 无需 os._exit 兜底（那是进程内 QThread 集成的历史包袱）
        self._we_closing = True  # 关闭流程标记（窗口自愈跳过）
        self._trace_close("closeEvent entered")
        # 0.6.0 完善：录制中关窗口先停录制（钩子线程残留会持续注入/记录）
        # R12（S2-B）：通过 recorder.stop_active_recorder() 公共 API 收尾——
        # 不再 reach-in `_active_recorder` 模块私有。
        try:
            from runtime.input.recorder import stop_active_recorder
            result = stop_active_recorder(save=True)
            if result is not None and result.get("saved_path") is not None:
                self._trace_close(
                    f"closeEvent 录制收尾保存: {result['saved_path'].name}"
                    f"（{result['event_count']} 事件）")
        except Exception as e:
            logging.getLogger("gui.main_window").warning(
                "录制收尾保存失败: %s", e)
        if getattr(self, "_health_worker", None) is not None and self._health_worker.isRunning():
            self._health_worker.requestInterruption()
            if not self._health_worker.wait(3000):
                import logging
                logging.getLogger("gui.main_window").warning(
                    "HealthWorker 关闭超时（线程可能残留）")
        # 窗口销毁时取消事件订阅（防已删 Qt 信号被后续 publish 调用）
        # BUG-011：取消订阅异常不阻断关闭链（各步隔离）
        try:
            if getattr(self, "event_bus", None) is not None:
                self.event_bus.unsubscribe(self._on_runtime_event)
        except Exception:
            import logging
            logging.getLogger("gui.main_window").exception("取消事件订阅失败")
        self._trace_close("closeEvent accepted — normal quit")
        self.shutdown()
        event.accept()
        # quitOnLastWindowClosed=False：显式退出事件循环（正常关闭路径唯一出口）
        try:
            from PySide6.QtWidgets import QApplication as _QA
            _app = _QA.instance()
            if _app is not None:
                _app.quit()
        except Exception as e:
            logging.getLogger("gui.main_window").debug(
                "QApplication.quit() 失败: %s", e)

    def refresh_command_deck(self):
        """录制同步后刷新指挥台（目标下拉 + 任务队列联动更新）。

        S6（S2-B）：业务下沉到 knowledge.guides_loader.refresh_all_targets()
        ——本方法只剩"取结果 → 通知 deck"两步薄包装。
        """
        try:
            # 业务：同步自定义地图 + 聚合全部目标（保留用户勾选状态）
            from knowledge.guides_loader import refresh_all_targets
            targets = refresh_all_targets(include_custom=True)
            self._all_targets = targets
            deck = getattr(self, "command_deck", None)
            if deck is not None and hasattr(deck, "refresh_targets"):
                deck.refresh_targets(targets)
        except Exception as e:
            import logging
            logging.getLogger("gui.main_window").exception(
                "指挥台刷新失败: %s", e)

    def _on_guide_run(self, mdir, region):
        """攻略体系"执行此区域"：匹配知识包该区域的已采集目标 → 指挥台执行。

        知识包（black_tower_test）workflow 的 room 与地图集 region 同名；
        无匹配（骨架/其他地图）→ 明确提示。"""
        try:
            from runtime.knowledge_loader import KnowledgePackage
            from config.settings import ROOT
            pkg = KnowledgePackage(ROOT / "knowledge" / "source" / "black_tower_test")
            matched = []
            for c in pkg.chests:
                wf = pkg.workflow(c["id"])
                room = (wf or {}).get("room") if wf else None
                if room == region:
                    matched.append(c["id"])
            if not matched:
                deck = self._deck()
                if deck is not None:
                    deck.led.setText(
                        f"● 区域 {region} 暂无已采集点位（知识包无匹配 workflow）")
                    # U-06: 统一到 WARN
                    # D6（S2-B）：走 _led_style(token) 单一入口。
                    deck.led.setStyleSheet(_led_style(WARN))
                return
            # 切到指挥台并选中该区域 → 直接执行 matched（combo 匹配是 UX 高亮，
            # 执行不依赖 combo——之前 payload[1]==region 中文vs英文恒 False，
            # 导致实际跑的是"全部目标"）
            self.navigationInterface.setCurrentWidget(self.command_deck)
            deck = self.command_deck
            idx = deck.find_region_index(region) if hasattr(deck, "find_region_index") \
                else None
            if idx is not None:
                deck.target_combo.setCurrentIndex(idx)
            self._start_run(matched)
        except Exception:
            import logging
            logging.getLogger("gui.main_window").exception("攻略区域执行接线失败")

    @gui_safe
    def _start_run(self, targets):
        # 防重复启动（连点/事件未达窗口期）
        # 审查 P0-6：stopped（手动停止）/gate_blocked（G3 拦截）也可重启——
        # 原集合缺这两态导致停止后按钮恢复但点击永久静默
        # 审查：state 可能是 MissionState 枚举或字符串——统一取 value 比较
        deck = self._deck()
        _st = getattr(self.mission_controller.state, "value",
                      self.mission_controller.state)
        if _st not in (
                "idle", "done", "crashed", "invalid",
                "stopped", "gate_blocked"):
            if _st == "paused":
                # 审查：paused 禁止启动——旧 runner 线程仍在人工介入/abort
                # 路径，此时 start 会双执行；明确提示而非静默（原静默 return
                # 造成"暂停后无法恢复"困惑）
                if deck is not None:
                    deck.led.setText(
                        "● 任务已暂停（人工介入）——等待自然结束或按 F10 停止后重试")
                    # U-06: 统一到 WARN
                    # D6（S2-B）：走 _led_style(token) 单一入口。
                    deck.led.setStyleSheet(_led_style(WARN))
            return
        # 真机前置校验（G3 会拦，但提前告知更好）
        # foreground 不拦截——程序会自动拉游戏置顶，
        # 用户点开始瞬间游戏不在前台是常态，拦截=点开始就报环境不对。
        # 仅 admin 硬拦截（非管理员输入被 UIPI 拦，拉了也白拉）。
        if deck is not None and getattr(self, "_health", None):
            h = self._health
            warns = []
            if h.get("admin") is False:
                warns.append("非管理员（输入会被 UIPI 拦截）")
            if warns:
                deck.led.setText("● 真机执行需先处理: " + "；".join(warns))
                # U-06: 统一到 WARN
                # D6（S2-B）：走 _led_style(token) 单一入口。
                deck.led.setStyleSheet(_led_style(WARN))
                deck.start_btn.setEnabled(True)
                deck.stop_btn.setEnabled(False)
                return
            if h.get("foreground") is False:
                # 提示但不阻断——启动流程会自动拉游戏置顶
                deck.led.setText("● 游戏窗口不在前台——将自动拉置顶")
                # U-06: 统一到 WARN
                # D6（S2-B）：走 _led_style(token) 单一入口。
                deck.led.setStyleSheet(_led_style(WARN))
        if deck is not None:
            deck.reset()
            deck.set_starting()  # 同步禁按钮（不依赖事件）
        # 启动即时反馈（0.6.0：静默期=用户感知"没动静"）
        try:
            if deck is not None:
                deck.set_run_status("● 启动中：检查环境…", busy=True)
        except Exception as e:
            logging.getLogger("gui.main_window").debug(
                "deck.set_run_status() 失败: %s", e)
        # 前台守护：开始任务即持续拉游戏置顶（用户要求）
        self.start_foreground_watch()
        try:
            # 业务细节封装在 controller（路径/规格不再裸露在 GUI）
            self.mission_controller.start(targets)
        except Exception as e:
            # 同步异常直接反馈（按钮不永久卡死）
            # 修复：启动失败即停前台守护（不再疯狂拉置顶）
            self.stop_foreground_watch()
            if deck is not None:
                deck.led.setText("● 启动失败: " + str(e)[:100])
                deck.start_btn.setEnabled(True)
                deck.stop_btn.setEnabled(False)

    @gui_safe
    def _stop_run(self):
        deck = self._deck()
        if deck is not None:
            deck.set_stopping()  # 停止反馈先于结果
        # 修复：用户停止即停前台守护（不再抢前台）
        self.stop_foreground_watch()
        self.mission_controller.stop()

    def _on_runtime_event(self, event):
        # runner 线程可能调用本方法——只入队（deque append 原子），
        # 由主线程 QTimer 轮询消费（本环境跨线程 Qt 信号不可靠）。
        # 绝不在此直调 Qt 控件。
        self._runtime_queue.append(event)

    def _poll_runtime_events(self):
        """主线程轮询消费 runtime 事件队列（50ms）——合并刷新（）。

        审查：deque.popleft 原子消费，不整体替换列表——原 list 替换在
        runner append 与主线程交换引用之间竞态会丢事件。
        """
        q = self._runtime_queue
        if not q:
            return
        events = []
        while q:
            try:
                events.append(q.popleft())
            except IndexError:
                break
        if events:
            self._flush_events(events)

    def _flush_events(self, events):
        # GUI 线程内执行——所有 Qt 控件操作都在这里
        deck = self._deck()
        for event in events:
            # T4：观察中心已并入「出击」页——运行时事件统一进 deck.on_event()
            # 单入口（T1 的 on_event 内部先 _track_event 记统计/时间线，随后
            # 再走执行事件分支），不再有第二个页面消费者（原 self.observation
            # 分支已删除，避免统计/时间线随页面删除静默消失）。
            # t32 防御性补齐（B-01）：deck.on_event 异常隔离——
            # 单事件异常不得影响本批其它事件分发。④区复核已证明该路径
            # 当前不可触发（三层防护），本条为防御性补齐。
            if deck is not None:
                try:
                    deck.on_event(event)
                except Exception:
                    import logging
                    logging.getLogger("gui.main_window").exception(
                        "deck.on_event 异常（已隔离，不影响本批其它事件）")
            # 提示音接线 ：安全网——任务失败/暂停/人工介入/失败收尾
            # 让"人在旁边看别处"的用户能听见。开关 QSettings ``alert_sound``
            # 与设置页写入侧同键；发声失败只记日志（见 gui.sound.play）——
            # 不弹对话框、不影响主链路。映射规则集中在 gui.sound.on_runtime_event
            # （fail_recorded / pause_requested / human_intervention /
            # run_finished[result ∈ crashed|invalid|some_failed|gate_blocked]），
            # 其余事件不发声（见模块 docstring）。
            try:
                _on_runtime_sound(event)
            except Exception:
                import logging
                logging.getLogger("gui.main_window").exception(
                    "提示音接线异常（已忽略，不影响任务主链路）")
            # 失败持久化（修复：失败日志落盘 memory/failures.jsonl——
            # 原来只发瞬时事件，重启即丢，失败检查器也是摆设）
            if event.type == "fail_recorded":
                try:
                    from runtime.failure_memory import FailureMemory
                    ctx = event.context or {}
                    FailureMemory().record(
                        failure=event.detail or "unknown",
                        context={"target": ctx.get("target"),
                                 "category": ctx.get("category"),
                                 "error": ctx.get("error"),
                                 "ts_human": event.created_at
                                 if hasattr(event, "created_at") else None})
                except Exception as e:
                    logging.getLogger("gui.main_window").debug(
                        "FailureMemory().record() 失败: %s", e)
                # F3 修复 ：EmergencyMonitor 启动失败——用户可见反馈。
                # 原 orchestrator._orchestrator_core.py:105-115 只 logging.warning，
                # 用户不知道人机保护（光标/前台/Esc → human_intervention 暂停）
                # 已静默失效。这里走 deck LED 显示「人机保护已失效」。
                if event.detail and "F3" in str(event.detail) \
                        and "emergency_monitor" in str(event.detail):
                    if deck is not None:
                        try:
                            deck.led.setText("⚠ 人机保护已失效（F3）")
                            deck.led.setStyleSheet(_led_style(WARN))
                        except Exception as e:
                            logging.getLogger("gui.main_window").debug(
                                "deck.led.setText/setStyleSheet 失败: %s", e)
            # 审查 P1：完成状态持久化接线——目标 done 时记录（原实现是死代码）
            # 注：补 None 防护——event.context 缺失时（部分事件类型）`(or {})`
            # 兜底，避免 AttributeError 中断当批 flush（修复 U-33）
            # DS 审计 ：run_mission 收尾已做同 pkg_key 落盘——此处先
            # is_done 幂等检查；已存在则跳过，保住收尾侧优质 meta
            # （rarity/room/workflow），防止无参 record_completed 覆盖。
            ctx = event.context or {}
            if event.type == "target_progress" \
                    and ctx.get("status") == "done" \
                    and ctx.get("target"):
                try:
                    import runtime.chest_state as _cs
                    _done = _cs.is_done(
                        self.mission_controller._pkg_key(), ctx["target"])
                    if not _done:
                        self.mission_controller.record_completed(
                            [ctx["target"]])
                except Exception:
                    import logging
                    logging.getLogger("gui.main_window").exception(
                        "完成状态持久化失败")
            # 任务结束/报错 → HUD 收起（不再卡在游戏窗口）+ 停前台守护
            # （修复：失败后不再疯狂拉游戏置顶）
            if event.type in ("run_finished", "pause_requested") \
                    and getattr(self, "_hud", None) is not None:
                try:
                    self._hud.hide()
                except Exception as e:
                    logging.getLogger("gui.main_window").debug(
                        "HUD.hide() 失败: %s", e)
            if event.type == "run_finished":
                self.stop_foreground_watch()
