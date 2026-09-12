"""SettingsPage「作坊」：完整设置页（三页重做 t3——P3 大扩）。

四个区块：
  ① 回放灵敏度（REPLAY_SENSITIVITY 1-5，与游戏内灵敏度一致）
  ② 路径与默认地图（只读路径信息 + DEFAULT_MAP 可改）
  ③ 健康检测（零副作用探测：后台线程跑 check_health，QTimer 轮询收结果）
  ④ 提示音开关（QSettings alert_sound；页内真实消费：健康异常提醒 + 试听）


  快捷键（F10 全局紧急停止）为只读信息，改为内容区末尾一行提示，不占卡片。
  保存入口统一为页脚的「保存全部设置」；卡片内不再重复放保存按钮。

持久化只沿用本项目既有两条通道（不引入新框架、不加 config 模块新键）：
config.settings.set_override / get —— 运行时覆盖，当前进程立即生效；
QSettings(QSETTINGS_ORG, QSETTINGS_APP) —— GUI 记忆，下次启动带入。
  注册表后端写不可达时（沙箱/CI 报 AccessError）自动降级落
  settings/gui_settings.ini（.gitignore 已排除该名，不入库）——
  见 _SettingsStore；读侧两后端任一保存过即可回读。
密钥例外：API key 只进 set_override（进程内存），绝不写 QSettings /
仓库文件 / 日志 / UI 回显（任务契约第 4 条）。

跨线程：health 探测在 daemon 线程执行，结果经 QTimer 主线程轮询消费——
不跨线程 emit Qt 信号（README 开发铁律）。
"""
import threading

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSpinBox)
from qfluentwidgets import CardWidget

from gui.pages.base_page import (BasePage, FONT_BODY, FONT_CAPTION,
                                 card_layout, card_title)
# U-36：单一色源——色值/字号一律从 theme.py / base_page.py token 取。
from gui.theme import DANGER, OK, TEXT_MUTED, WARN
from config.settings import QSETTINGS_APP, QSETTINGS_ORG

# health 全量行（键名与 runtime/health.py 的 result dict 一一对应）
_HEALTH_ROWS = (
    ("window", "游戏窗口"), ("capture", "后台截图"), ("ocr", "文字识别引擎"),
("foreground", "前台锁定"), ("admin", "管理员权限"),
    ("input", "输入汇总"), ("input_l0", "输入 L0 光标回读"),
    ("input_l1", "输入 L1 注入能力"), ("input_l2", "输入 L2 游戏响应"),
    ("ffmpeg", "视频工具"), ("disk", "磁盘空间"),
)


def _cfg(key, default=None):
    """config.settings.get 薄封装（运行时覆盖 > 环境变量 > .env > 默认）。"""
    from config import settings as _s
    return _s.get(key, default)


def _mask_key(key):
    """密钥掩码：只显示前 4 位 + 位数（契约第 4 条），不足 8 位不露前缀。"""
    if not key:
        return "未配置"
    k = str(key)
    if len(k) >= 8:
        return f"{k[:4]}****（共 {len(k)} 位）"
    return f"已配置（共 {len(k)} 位）"


class _SettingsStore:
    """QSettings 双后端门面：注册表优先，写不可达自动落 ini 文件。

    场景：沙箱/CI 等受限环境注册表写入报 AccessError——裸用 QSettings
    会静默丢设置（status 只能事后查）。这里写入失败即转落
    settings/gui_settings.ini（.gitignore 已含该名，不入库）；读侧
    注册表缺键且不可写时回看 ini——任一后端保存过即可回读。
    """

    def __init__(self):
        self._reg = QSettings(QSETTINGS_ORG, QSETTINGS_APP)
        self._ini = None

    def _fallback(self):
        if self._ini is None:
            from config.settings import data_path
            self._ini = QSettings(str(data_path("settings") / "gui_settings.ini"),
                                  QSettings.Format.IniFormat)
        return self._ini

    def value(self, key, default=None, type=None):  # noqa: A002 — PySide6 签名同名
        got = (self._reg.value(key, default, type)
               if type is not None else self._reg.value(key, default))
        if (got is None or got == default) and \
                self._reg.status() == QSettings.Status.AccessError:
            ini = self._fallback()
            return (ini.value(key, default, type)
                    if type is not None else ini.value(key, default))
        return got

    def setValue(self, key, value):
        """写注册表；注册表 AccessError 时自动落 ini 文件。

        韧性（真实使用不静默）：
        注册表写失败 → 立即转落 ini（.gitignore 已排除 gui_settings.ini）；
        ini 也写失败 → 抛 OSError，由调用方 `_save_*` 兜底 QMessageBox 反馈；
          双后端全不可达时用户必须看得见，不能假装保存成功。
        """
        self._reg.setValue(key, value)
        self._reg.sync()
        if self._reg.status() == QSettings.Status.AccessError:
            ini = self._fallback()
            ini.setValue(key, value)
            ini.sync()
            if ini.status() != QSettings.Status.NoError:
                raise OSError(
                    f"QSettings 双后端均不可写：注册表={self._reg.status().name}"
                    f"，ini={ini.status().name}")

    def status(self):
        """合并状态：注册表优先，AccessError 时退回看 ini。

        供 `_save_all` 结尾判断双后端健康状况（用于最终状态条提示）。
        """
        if self._reg.status() != QSettings.Status.AccessError:
            return self._reg.status()
        ini = self._fallback()
        return ini.status()


def _store():
    """进程级 _SettingsStore 单例（页面所有 QSettings 读写入口）。"""
    global _STORE
    if _STORE is None:
        _STORE = _SettingsStore()
    return _STORE


_STORE = None


class SettingsPage(BasePage):
    """「作坊」设置页：八区块，全部可读可改可存，重载可回读。"""
    # 内容高于可视区时滚动而非压扁卡片（本页卡片多，必须开启）
    scrollable = True


    def __init__(self, parent=None):
        super().__init__("设置", parent)
        self.set_status("密钥仅进程内生效（不写 .env / 不入库）")

        self._health_lock = threading.Lock()
        self._health_result = None   # 后台线程写入，主线程 QTimer 轮询消费
        self._health_running = False
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(200)
        self._health_timer.timeout.connect(self._poll_health)

        self._build_sensitivity_card()   # ① 回放灵敏度
        self._build_paths_card()         # ② 路径与默认地图
        self._build_health_card()        # ③ 健康检测
        self._build_sound_card()         # ④ 提示音开关
        self._add_shortcut_hint()        # 快捷键说明（只读信息，不占卡片）

        self.content_layout.addStretch(1)

        # Footer：全局保存（逐区块调用各自的幂等保存），右对齐（沿用旧页布局）
        save_btn = QPushButton("保存全部设置")
        save_btn.setFixedWidth(130)
        save_btn.clicked.connect(self._save_all)
        self.add_footer(save_btn)
        f = self.footer.layout()
        f.setDirection(QHBoxLayout.RightToLeft)

        self._load()

    # ① 灵敏度

    def _build_sensitivity_card(self):
        card = CardWidget()
        cl = card_layout(card)
        card_title(card, "① 回放灵敏度")

        row = QHBoxLayout()
        row.addWidget(QLabel("游戏内灵敏度："))
        self.sensitivity_spin = QSpinBox()
        self.sensitivity_spin.setRange(1, 5)
        self.sensitivity_spin.setFixedWidth(80)
        row.addWidget(self.sensitivity_spin)
        row.addStretch(1)
        cl.addLayout(row)

        hint = QLabel("1-5 档，与《崩坏：星穹铁道》设置项一致；录制时记入轨迹 JSON，"
                      "回放按「录制/回放」比例换算视角位移（录制者与回放者灵敏度可不同）")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: {FONT_CAPTION}px;")
        cl.addWidget(hint)
        self.content_layout.addWidget(card)

    def _save_sensitivity(self):
        try:
            from config import settings as _s
            v = int(self.sensitivity_spin.value())
            if not 1 <= v <= 5:
                raise ValueError(f"灵敏度 {v} 越界（合法 1-5）")
            _s.set_override("REPLAY_SENSITIVITY", str(v))
            _store().setValue("replay_sensitivity", v)
            self.set_status(f"已保存：REPLAY_SENSITIVITY={v}")
        except Exception as e:
            QMessageBox.critical(self, "设置",
                                 f"灵敏度保存失败: {type(e).__name__}: {e}")

    # 快捷键（只读信息，不是设置项——一行提示即可，不占一张卡）

    def _add_shortcut_hint(self):
        hint = QLabel(
            "快捷键：F10 全局紧急停止（系统级，游戏内也可触发）"
            "——释放全部按键 + 停任务/停录制 + HUD 提示")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: {FONT_CAPTION}px;")
        self.content_layout.addWidget(hint)

    # ② 路径

    def _build_paths_card(self):
        card = CardWidget()
        cl = card_layout(card)
        card_title(card, "② 路径与默认地图")

        self.paths_label = QLabel("")
        self.paths_label.setWordWrap(True)
        self.paths_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: {FONT_BODY}px;")
        cl.addWidget(self.paths_label)

        row = QHBoxLayout()
        row.addWidget(QLabel("默认地图："))
        self.map_combo = QComboBox()
        self.map_combo.setEditable(True)
        row.addWidget(self.map_combo, 1)
        cl.addLayout(row)

        btns = QHBoxLayout()
        btn2 = QPushButton("刷新")
        btn2.clicked.connect(self._refresh_paths)
        btns.addWidget(btn2)
        btns.addStretch(1)
        cl.addLayout(btns)
        self.content_layout.addWidget(card)

    def _refresh_paths(self):
        """只读路径 + DEFAULT_MAP 候选（真实扫描 knowledge/guides/maps）。"""
        info = self._env_info()
        self.paths_label.setText(info)
        items = []
        try:
            from config import settings as _s
            guides = _s.knowledge_root() / "guides" / "maps"
            if guides.is_dir():
                items = sorted(p.name for p in guides.iterdir() if p.is_dir())
        except Exception:
            items = []
        default_now = str(_cfg("DEFAULT_MAP", "") or _default_map_value())
        if default_now not in items:
            items.insert(0, default_now)
        self.map_combo.blockSignals(True)
        self.map_combo.clear()
        self.map_combo.addItems(items)
        self.map_combo.setCurrentText(default_now)
        self.map_combo.blockSignals(False)

    def _save_default_map(self):
        try:
            from config import settings as _s
            v = self.map_combo.currentText().strip()
            if not v:
                raise ValueError("默认地图不能为空")
            _s.set_override("DEFAULT_MAP", v)
            _store().setValue("default_map", v)
            self.set_status(f"已保存：DEFAULT_MAP={v}")
        except Exception as e:
            QMessageBox.critical(self, "设置",
                                 f"默认地图保存失败: {type(e).__name__}: {e}")

    # ③ health

    def _build_health_card(self):
        card = CardWidget()
        cl = card_layout(card)
        card_title(card, "③ 健康检测（11 项，零副作用）")

        self.health_grid = QGridLayout()
        self.health_grid.setHorizontalSpacing(18)
        cl.addLayout(self.health_grid)

        self.health_errors = QLabel("尚未检测——点击右下按钮运行（零副作用："
                                    "不抢前台 / 不动鼠标 / 不向游戏按键）")
        self.health_errors.setWordWrap(True)
        self.health_errors.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: {FONT_CAPTION}px;")
        cl.addWidget(self.health_errors)

        self.health_btn = QPushButton("运行健康检测")
        self.health_btn.clicked.connect(self._run_health)
        cl.addWidget(self.health_btn)
        self.content_layout.addWidget(card)
        self._render_health_rows(None)

    def _render_health_rows(self, cap):
        """渲染 12 项健康行；cap=None 显示「未测」。主线程调用。"""
        for i, (key, label) in enumerate(_HEALTH_ROWS):
            r, c = divmod(i, 3)
            state = None if cap is None else cap.get(key)
            if state is None:
                text, color = "未测", WARN
            elif state:
                text, color = "正常", OK
            else:
                text, color = "异常", DANGER
            w = self.health_grid.itemAtPosition(r, c)
            if w is not None:
                w.widget().setText(f"{label}：{text}")
                w.widget().setStyleSheet(f"color: {color};")
                continue
            lbl = QLabel(f"{label}：{text}")
            lbl.setStyleSheet(f"color: {color};")
            self.health_grid.addWidget(lbl, r, c)

    def _run_health(self):
        """后台线程跑 check_health（game_required/input_probe/auto_activate 全关
        ——零副作用），主线程 QTimer 轮询收结果（不跨线程信号）。"""
        if self._health_running:
            return
        self._health_running = True
        self._health_result = None
        self.health_btn.setEnabled(False)
        self.set_status("健康检测中…", busy=True)

        def _work():
            try:
                from runtime.health import check_health
                res = check_health(game_required=False, input_probe=False,
                                   auto_activate=False)
            except Exception as e:
                res = {"capability": {},
                       "errors": {"health": f"{type(e).__name__}: {e}"},
                       "all_ok": False}
            with self._health_lock:
                self._health_result = res

        threading.Thread(target=_work, daemon=True,
                         name="settings-health-check").start()
        self._health_timer.start()

    def _poll_health(self):
        with self._health_lock:
            res = self._health_result
        if res is None:
            return
        self._health_timer.stop()
        self._health_running = False
        self._health_result = None
        self.health_btn.setEnabled(True)
        self._render_health_rows(res.get("capability") or {})
        errors = res.get("errors") or {}
        if errors:
            self.health_errors.setText(
                "异常详情：\n" + "\n".join(f"· {k}: {v}"
                                          for k, v in sorted(errors.items())))
            self.health_errors.setStyleSheet(
                f"color: {DANGER}; font-size: {FONT_CAPTION}px;")
        else:
            self.health_errors.setText("全部通过（零副作用探测，未含 L0/L2 注入项）")
            self.health_errors.setStyleSheet(
                f"color: {OK}; font-size: {FONT_CAPTION}px;")
        self.set_status("健康检测完成：全部正常" if res.get("all_ok")
                        else "健康检测完成：存在异常项（见上方详情）")
        # ⑧提示音的页内真实消费点：检测发现异常且开关开启 → 提示音
        if not res.get("all_ok") and self._sound_enabled():
            QApplication.beep()

    # ④ 提示音

    def _build_sound_card(self):
        card = CardWidget()
        cl = card_layout(card)
        card_title(card, "④ 提示音")

        self.sound_check = QCheckBox("启用提示音（健康检测发现异常时蜂鸣提醒）")
        cl.addWidget(self.sound_check)

        row = QHBoxLayout()
        test_btn = QPushButton("试听提示音")
        test_btn.clicked.connect(self._test_sound)
        row.addWidget(test_btn)
        row.addStretch(1)
        cl.addLayout(row)

        hint = QLabel("开关保存后立即生效，下次启动仍保持；"
                      "任务失败、等待人工处理、异常结束时会自动发声提醒")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: {FONT_CAPTION}px;")
        cl.addWidget(hint)
        self.content_layout.addWidget(card)

    def _sound_enabled(self):
        try:
            return _store().value("alert_sound", False, type=bool)
        except Exception:
            return False

    def _test_sound(self):
        QApplication.beep()  # 立即反馈，无论开关状态（试听语义）

    def _save_sound(self):
        try:
            _store().setValue("alert_sound",
                              bool(self.sound_check.isChecked()))
            self.set_status(
                f"已保存：提示音={'开' if self.sound_check.isChecked() else '关'}")
        except Exception as e:
            QMessageBox.critical(self, "设置",
                                 f"提示音保存失败: {type(e).__name__}: {e}")

    # 读取/保存

    def _load(self):
        """回读全部区块（可重复调用——幂等）。优先级：运行时覆盖 > QSettings > 配置。"""
        s = _store()
        # ② 灵敏度：与 command_deck._toggle_record 同款校验（1-5，默认 4）
        try:
            raw = _cfg("REPLAY_SENSITIVITY", "4")
            f = float(raw) if raw else 4.0
            v = int(f) if 1 <= f <= 5 else 4
        except Exception:
            v = 4
        self.sensitivity_spin.setValue(v)

        # ③ 路径：只读路径 + DEFAULT_MAP
        self._refresh_paths()
        saved_map = s.value("default_map", "", type=str)
        if saved_map:
            self.map_combo.setCurrentText(saved_map)

        # ④ health：不自动跑（offscreen 构造必须轻量）——按钮触发
        # ⑤ 提示音
        self.sound_check.setChecked(self._sound_enabled())

    def _save_all(self):
        """全局保存：逐区块调用各自的幂等保存（失败各自弹可见错误）。

        韧性：末尾再核查一次 _SettingsStore 双后端状态——若注册表 AccessError
        且 ini 也非 NoError，弹可见警告（不能"全保存完成"但实际都失败）。
        """
        self._save_sensitivity()
        self._save_default_map()
        self._save_sound()
        try:
            st = _store().status()
            if st != QSettings.Status.NoError:
                QMessageBox.warning(
                    self, "设置",
                    f"设置部分保存失败：QSettings 状态={st.name}"
                    "（注册表与 ini 文件后端均可能不可用）")
        except Exception as e:
            QMessageBox.critical(self, "设置",
                                 f"设置状态核查失败: {type(e).__name__}: {e}")
        self.set_status("已保存全部区块（密钥仅进程内生效）")

    # 环境信息

    @staticmethod
    def _env_info():
        import sys
        try:
            from config import settings as _s
            knowledge = _s.knowledge_root()
            db = _s.runtime_db_path()
            env_file = knowledge.parent / ".env"
        except Exception:
            knowledge = db = env_file = None
        lines = [
            f"知识库：{knowledge}" if knowledge else "知识库：未知",
            f"运行库：{db}" if db else "运行库：未知",
            (f".env: {env_file}"
             f"{'（存在）' if env_file and env_file.exists() else '（不存在）'}"
             if env_file else ".env: 未知"),
            f"Python: {sys.version.split()[0]}",
        ]
        return "\n".join(lines)


def _default_map_value():
    """DEFAULT_MAP 的配置层默认值（config/settings.py 的 default_map）。"""
    try:
        from config import settings as _s
        return _s.default_map()
    except Exception:
        return "02_herta_space_station"
