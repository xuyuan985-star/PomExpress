"""gui.sound — 提示音统一入口（开关 QSettings ``alert_sound``；失败只记日志不静默）。

设计意图

单一真源：开关键名固定为 ``alert_sound``，与 ``gui/pages/settings.py``
  写入侧保持完全一致（命名空间取自 config.settings 的 QSETTINGS_* 常量）。主窗口与
  其他 GUI 组件一律通过本模块消费，不再各自读键、不再造第二个键名。
单一发声能力：仅使用 PySide6 ``QApplication.beep()``（Qt 平台原生
  WinMM 蜂鸣）——零新增依赖，与设置页页内健康异常 beep 用同一能力。
双后端读：仿 ``gui.pages.settings._SettingsStore`` 的做法，注册表
  写入不可达（沙箱/CI 环境 ``QSettings.Status.AccessError``）时回看
  ``settings/gui_settings.ini``——保证在受限环境下仍能读到设置页写入的
  值。这里只读，不写；写入侧仍归设置页。
永不静默：任何异常都在本模块内被捕获并 ``logger.exception``，主调用
  链（``MainWindow._flush_events`` → ``MainWindow._on_runtime_sound``）
  不会因发声失败被打断，也不会弹出打断性对话框。
主线程调用：本模块不做跨线程调度；主窗口已在 QTimer 主线程消费 runtime
  事件后同步调用 ``on_runtime_event``，符合"跨线程一律 QTimer 轮询"约束。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("gui.sound")

# 与 gui/pages/settings.py 完全一致的 QSettings 命名空间 + 键名
# （设置页写入侧：_store().setValue("alert_sound", bool(check.isChecked()))）
# 命名空间常量取自 config.settings，避免多处硬编码字符串漂移。
from config.settings import QSETTINGS_APP as _SETTINGS_APP
from config.settings import QSETTINGS_ORG as _SETTINGS_ORG

SOUND_KEY = "alert_sound"


def _ini_fallback():
    """注册表 AccessError 时回看的 ini 位置（与设置页 _SettingsStore 一致）。"""
    try:
        from PySide6.QtCore import QSettings
        from config.settings import data_path
        return QSettings(str(data_path("settings") / "gui_settings.ini"),
                         QSettings.Format.IniFormat)
    except Exception:
        logger.exception("提示音 ini 回退后端初始化失败（忽略）")
        return None


def is_enabled() -> bool:
    """读 QSettings ``alert_sound``（缺省 False；异常一律 False——安全网不假报开）。"""
    try:
        from PySide6.QtCore import QSettings
        s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        got = s.value(SOUND_KEY, False, type=bool)
        if (got is None or got is False) and \
                s.status() == QSettings.Status.AccessError:
            ini = _ini_fallback()
            if ini is not None:
                got = ini.value(SOUND_KEY, False, type=bool)
        return bool(got)
    except Exception:
        # 读设置失败一律视为关——避免误响打断用户；错误记日志便于排查
        logger.exception("提示音开关读取失败（按关闭处理）")
        return False


def _beep_now() -> None:
    """实际发声调用点（便于测试 monkeypatch）。

    走 ``QApplication.beep()``（Qt 内置，Windows 上映射到 WinMM MessageBox
    系统提示音）——零新增依赖。若 QApplication 尚未创建（例如单测仅 import
    本模块）则静默 no-op；主窗口的真实事件路径总是先有 QApplication。
    """
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        logger.debug("QApplication 未创建——提示音 no-op")
        return
    app.beep()


def play() -> None:
    """对外发声入口：先读开关，未开启直接返回；开启则 beep，异常一律记日志。

    调用方契约：
    主调用链的稳定性由本函数保证——**永不抛异常**、不弹对话框。
    关闭时**零副作用**（不读 ini、不调 QApplication、不打日志），便于
      测试断言"关闭时发声入口未被调用"。
    """
    if not is_enabled():
        return
    try:
        _beep_now()
    except Exception:
        # 发声失败必须"可见"：记日志（不静默）；同时不阻断任务主链路
        # （无 raise、无消息框）。
        logger.exception("提示音发声失败（已忽略，不影响任务主链路）")


def on_runtime_event(event: Any) -> None:
    """运行时事件 → 提示音映射（主窗口主线程同步调用）。

    映射规则（与"人在旁边协作跑，声音是安全网"的产品意图对齐）：
    ``fail_recorded`` → 发声：目标失败是安全网必须响的核心场景。
    ``pause_requested`` → 发声：暂停意味着"停下来等人"，必须让人听见。
    ``human_intervention`` → 发声：紧急介入/EMERGENCY 路径。
    ``run_finished`` → 仅当 ``context["result"]`` 属于失败集合
      ``{"crashed", "invalid", "some_failed", "gate_blocked"}`` 时发声：
      * ``crashed`` / ``invalid`` — 运行时/前置校验崩溃，用户必须知道；
      * ``some_failed`` — 有目标未达成，需要复盘；
      * ``gate_blocked`` — 环境不满足（G3 门槛拦截），需要人处理。
      而 ``stopped``（用户主动 F10 / stop）/ ``no_targets``（选目标前）/
      ``all_done``（成功）/ 空 result 保持沉默——不制造噪音、不误导。
    其它事件（``run_started`` / ``state_changed`` / ``target_progress``
      / ``observation`` / ``action_executed`` / ``mission_summary``）不发声：
      它们或为正常过程态（无失败/无紧急性），或已在 UI 层有明确文字
      反馈（deck 上的 LED/inspector），蜂鸣无新增信息量。

    本函数不检查开关——由 ``play()`` 内部把关；调用方无需分支。
    """
    if event is None:
        return
    try:
        etype = getattr(event, "type", None)
        if etype in ("fail_recorded", "pause_requested", "human_intervention"):
            play()
            return
        if etype == "run_finished":
            ctx = getattr(event, "context", None) or {}
            result = ctx.get("result", "") if isinstance(ctx, dict) else ""
            if result in ("crashed", "invalid", "some_failed", "gate_blocked"):
                play()
    except Exception:
        # 事件解析本身出错也不静默、不阻断主链路
        logger.exception("提示音事件解析失败（已忽略）")
