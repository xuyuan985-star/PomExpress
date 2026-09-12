"""WorldGraphPage：藏宝图（世界图 + 攻略体系 + 知识统计）。

P2「藏宝图」：world_graph + guides_view 吸收知识体系统计 + 移出录制按钮。
知识体系整页的"入库统计"卡片并入世界图顶条（不再单独占导航）。
录制按钮从世界图移出（由指挥台 P1 吸收）。
底操作行：刷新 + 地图导入 + 校验三个入口。
F1 修复（t6 评审）：三入口改 QProcess 非阻塞——原先在 GUI 主线程同步等待
  外部子进程（timeout 30/30/60s），期间主线程无法轮询热键队列，F10
  紧急停止最长被推迟 60s。范式同 task_center.TaskProcess：QProcess 的
  finished/errorOccurred/readyRead 由主线程事件循环投递（无跨线程 emit）。
"""
import json
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QMessageBox,
                               QPushButton, QWidget)
from qfluentwidgets import BodyLabel, CardWidget

from gui.pages.base_page import (BasePage, FONT_BODY, card_layout, card_title)
# U-36：单一色源——色值/字号一律取 theme.py / base_page.py token。
from gui.theme import DANGER, TEXT_MUTED, WARN

# 文件对话框起始目录与子进程工作目录：必须是**可写且稳定**的目录。
# 打包后 __file__ 指向临时解包目录（退出即删），拿它当起始目录会让用户
# 落在临时路径上，故取 DATA_ROOT（打包后 = exe 同级）。
from config.settings import DATA_ROOT as ROOT  # noqa: E402


class WorldGraphPage(BasePage):
    """藏宝图：地图级可视化 + 攻略体系 + 知识统计。"""

    # 不开页面级滚动：本页自身内容自然高度约 295px（默认窗口可视区约 574px），
    # 且内嵌的 GuidesView 自带 QScrollArea——再加一层会形成嵌套滚动。
    scrollable = False

    def __init__(self, parent=None):
        super().__init__("藏宝图", parent)
        self.set_status("地图级执行视图 · 攻略体系 · 知识统计")

        # 知识统计卡片（吸收自知识体系页——整页并入，不再占导航）
        self.stats_card = CardWidget()
        card_title(self.stats_card, "入库统计")
        self.stats_label = BodyLabel("正在扫描…")
        self.stats_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: {FONT_BODY}px;")
        card_layout(self.stats_card).addWidget(self.stats_label)
        self.content_layout.addWidget(self.stats_card)

        # 攻略体系视图（嵌入，不重复页头）
        from gui.pages.guides_view import GuidesView
        self._view = GuidesView(self).set_embedded()
        self.content_layout.addWidget(self._view, 1)

        # 底操作行：刷新 + 地图导入 + 校验
        footer = QWidget()
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(0, 0, 0, 0)

        refresh_btn = QPushButton("刷新统计")
        refresh_btn.setFixedWidth(110)
        refresh_btn.clicked.connect(self._refresh_stats)
        fl.addWidget(refresh_btn)

        map_btn = QPushButton("地图导入")
        map_btn.setToolTip("选择 外部参考实现 map JSON → 转换为 workflow v1.3")
        map_btn.clicked.connect(self._import_map)
        fl.addWidget(map_btn)
        self.map_btn = map_btn

        verify_btn = QPushButton("校验")
        verify_btn.setToolTip("全库完整性扫描（知识包 + 攻略库）")
        verify_btn.clicked.connect(self._validate_all)
        fl.addWidget(verify_btn)
        self.verify_btn = verify_btn

        fl.addStretch(1)
        self.add_footer(footer)
        self.footer.layout().setDirection(QHBoxLayout.RightToLeft)

        # F1 修复：后台任务状态（QProcess 非阻塞——单飞 + 超时看门狗）
        self._proc = None            # 活动中的 QProcess（None=空闲）
        self._proc_title = ""
        self._proc_output = ""
        self._proc_timed_out = False
        self._proc_timeout_s = 0
        self._proc_kill_conn = None  # destroyed→kill 连接句柄（收尾时摘除）
        self._proc_watchdog = QTimer(self)
        self._proc_watchdog.setSingleShot(True)
        self._proc_watchdog.timeout.connect(self._on_proc_timeout)

        # 延迟刷新（页面初始化不阻塞 UI）
        QTimer.singleShot(0, self._refresh_stats)

    # 知识统计

    def _refresh_stats(self):
        """刷新知识统计（从 guides_view 复用 GUIDES/POINT_FILES）。

        韧性（真实使用不静默）：
        GUIDES 不存在 → 可见文案 + WARN 色（原行为保留）。
        GUIDES 空 → 可见「未找到地图数据」提示，不再静默显示 0。
        损坏 JSON（map.json / points / areas） → 顶部统计行加红字警告 +
          日志记录，不再 `continue` 静默跳过（用户看不到"0 个"其实是文件损坏）。
        """
        from gui.pages.guides_view import GUIDES, POINT_FILES
        if not GUIDES.exists():
            self.stats_label.setStyleSheet(
                f"color: {WARN}; font-size: {FONT_BODY}px;")
            self.stats_label.setText(f"⚠ 攻略库不存在：{GUIDES}（请先导入知识库）")
            return

        map_count = 0
        area_count = 0
        point_count = 0
        ready_count = 0    # 可执行（有坐标）
        pending_count = 0  # 待采集（骨架空位）
        broken = []
        for md in sorted(GUIDES.iterdir()):
            if not md.is_dir():
                continue
            map_count += 1
            # map.json 是地图根元数据——损坏同样不静默（否则地图显示为空）
            map_json = md / "map.json"
            if map_json.exists():
                try:
                    json.loads(map_json.read_text(encoding="utf-8"))
                except Exception as e:
                    broken.append(f"{md.name}/map.json ({type(e).__name__})")
            areas = list((md / "areas").glob("*.json")) \
                if (md / "areas").exists() else []
            area_count += len(areas)
            for a in areas:
                try:
                    json.loads(a.read_text(encoding="utf-8"))
                except Exception as e:
                    # 区域 JSON 损坏同样不静默跳过——纳入 broken 列表提示
                    broken.append(f"{md.name}/areas/{a.name} ({type(e).__name__})")
            for f in POINT_FILES:
                pf = md / "points" / f
                if not pf.exists():
                    continue
                try:
                    pts = json.loads(pf.read_text(encoding="utf-8"))
                except Exception as e:
                    broken.append(f"{md.name}/points/{f} ({type(e).__name__})")
                    continue
                point_count += len(pts)
                for pt in pts:
                    if pt.get("status") == "empty" or pt.get("x") is None:
                        pending_count += 1
                    else:
                        ready_count += 1

        if broken:
            # 静默吞 → 用户以为地图 0 个；此处 log + 底部红字警告
            logging.getLogger("gui.world_graph").warning(
                "知识统计扫描发现损坏 JSON（%d 个）：%s",
                len(broken), ", ".join(broken[:5]))

        if map_count == 0:
            self.stats_label.setStyleSheet(
                f"color: {WARN}; font-size: {FONT_BODY}px;")
            self.stats_label.setText(
                f"⚠ 未找到地图数据（{GUIDES} 为空）——请先导入攻略包或"
                "地图再刷新")
            return

        style = (f"color: {DANGER}; font-size: {FONT_BODY}px;"
                 if broken else f"color: {TEXT_MUTED}; font-size: {FONT_BODY}px;")
        self.stats_label.setStyleSheet(style)
        text = (f"地图 {map_count} 个 · 区域 {area_count} 个 · 点位 {point_count} 条"
                f"（可执行 {ready_count} / 待采集 {pending_count}）")
        if broken:
            text += (f"\n⚠ {len(broken)} 个损坏 JSON 已跳过"
                     + (f"（{broken[0]}{', …' if len(broken) > 1 else ''}）"
                        if broken else ""))
        self.stats_label.setText(text)

    # 底操作行（均走 ingest 层）
    # F1 修复：各入口一律 QProcess 非阻塞（禁止 GUI 主线程同步等子进程——
    # 主线程阻塞期间热键队列无人轮询，F10 紧急停止被推迟至多 60s）。

    def _module_argv(self, module, *tail):
        """构造「执行某模块」的子进程 argv。

        打包版 `sys.executable` 就是 exe 本身，直接传 `-m 模块` 会被启动器
        当未知参数忽略、进而**开出一个新 GUI 窗口**（功能静默失效）。故打包版
        走 `--run <模块>` 桥（`app/launcher.py` 用 runpy 执行），源码版照旧用
        `python -m <模块>`。
        """
        if getattr(sys, "frozen", False):
            return [sys.executable, "--run", module, *tail]
        return [sys.executable, "-m", module, *tail]

    def _import_map(self):
        """地图导入：选择 地图 JSON → 非阻塞调用 map_converter。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 地图 JSON", str(ROOT),
            "JSON 文件 (*.json);;所有文件 (*)")
        if not path:
            return
        self._run_bg(
            self._module_argv("ingest.compiler.map_converter", path),
            "地图导入", 30)

    def _validate_all(self):
        """校验：非阻塞调用 app.validate_all 全库扫描。"""
        self._run_bg(self._module_argv("app.validate_all"), "校验", 60)

    # F1 修复：非阻塞外部任务（QProcess，范式同 task_center）

    def _run_bg(self, args, title, timeout_s):
        """以 QProcess 非阻塞运行外部命令。

        QProcess 的 finished/errorOccurred/readyRead 由主线程事件循环
          投递（同线程通知，非跨线程 emit——README 铁律不受影响）；
        单飞：同一时刻最多一个任务（重复触发给可见警告）；
        超时看门狗：超时 kill 并可见报错（不静默）；
        运行中三入口按钮禁用 + 状态条可见，完成/失败弹窗反馈。
        """
        if self._proc is not None:
            QMessageBox.warning(
                self, title,
                f"已有任务在运行：{self._proc_title}——请等待其完成后再试")
            return
        proc = QProcess(self)
        proc.setProgram(args[0])
        proc.setArguments(args[1:])
        proc.setWorkingDirectory(str(ROOT))
        # 合并 stdout/stderr——与旧 capture_output 展示行为一致（只取尾部）
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc = proc
        self._proc_title = title
        self._proc_output = ""
        self._proc_timed_out = False
        self._proc_timeout_s = timeout_s
        proc.readyReadStandardOutput.connect(self._on_proc_ready)
        proc.finished.connect(self._on_proc_finished)
        proc.errorOccurred.connect(self._on_proc_error)
        self._proc_kill_conn = self.destroyed.connect(proc.kill)  # 页面销毁不留孤儿
        for b in (self.map_btn, self.verify_btn):
            b.setEnabled(False)
        self.set_status(f"▶ {title} 运行中…（非阻塞，F10 随时可响应）", busy=True)
        self._proc_watchdog.start(timeout_s * 1000)
        proc.start()

    def _on_proc_ready(self):
        """渐进缓冲输出（尾部 500 字足够反馈，防大输出撑内存）。"""
        if self._proc is not None:
            self._proc_output += bytes(
                self._proc.readAllStandardOutput()).decode("utf-8", "replace")
            self._proc_output = self._proc_output[-2000:]

    def _on_proc_timeout(self):
        """看门狗：超时 kill——不静默，状态条 + 后续 finished 弹窗双报。"""
        proc = self._proc
        if proc is None:
            return
        self._proc_timed_out = True
        proc.kill()
        self.set_status(
            f"✖ {self._proc_title} 超时（>{self._proc_timeout_s}s）——已终止")

    def _on_proc_error(self, error):
        """FailedToStart 时 finished 不会来——必须在此收尾（不静默吞）。"""
        if error == QProcess.ProcessError.FailedToStart:
            self._finish_bg_failed("进程启动失败（解释器或脚本路径不可用）")

    def _finish_bg_failed(self, note):
        title = self._proc_title
        self._cleanup_bg()
        QMessageBox.warning(self, f"{title}失败", note)
        self.set_status(f"✖ {title}：{note}")

    def _on_proc_finished(self, exit_code, exit_status):
        if self._proc is None:
            return
        out = self._proc_output + bytes(
            self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        title = self._proc_title
        timed_out = self._proc_timed_out
        self._cleanup_bg()
        tail = out.strip()[-500:]
        if timed_out:
            QMessageBox.warning(
                self, title,
                f"超时被终止（返回码 {exit_code}）\n\n{tail}" if tail
                else f"超时被终止（返回码 {exit_code}）")
            self.set_status(f"✖ {title} 超时被终止")
        elif exit_status != QProcess.ExitStatus.NormalExit or exit_code != 0:
            QMessageBox.warning(
                self, f"{title}失败",
                f"返回码 {exit_code}\n\n{tail}" if tail
                else f"返回码 {exit_code}")
            self.set_status(f"✖ {title} 失败（返回码 {exit_code}）")
        else:
            QMessageBox.information(
                self, title,
                f"完成（返回码 0）\n\n{tail}" if tail else "完成（返回码 0）")
            self.set_status(f"✔ {title} 完成")

    def _cleanup_bg(self):
        """收尾：停看门狗、释放 QProcess、恢复按钮（幂等）。"""
        self._proc_watchdog.stop()
        if self._proc_kill_conn is not None:
            try:
                self.destroyed.disconnect(self._proc_kill_conn)
            except Exception:
                pass
            self._proc_kill_conn = None
        if self._proc is not None:
            self._proc.deleteLater()
        self._proc = None
        self._proc_title = ""
        self._proc_output = ""
        self._proc_timed_out = False
        self._proc_timeout_s = 0
        for b in (self.map_btn, self.verify_btn):
            b.setEnabled(True)

    # 录制安全 no-op（F10 在 t4 改造前仍会调它）

    def stop_recording(self):
        """安全 no-op（录制控件已迁往 P1 指挥台；F10 在 t4 改造前仍会调它）。"""
        pass
