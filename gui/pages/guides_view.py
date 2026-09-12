"""GuidesView（重构）：左侧=大地图列表，右侧=选中地图的区域卡片（点位合并）。"""
import json
import logging
from collections import defaultdict
from pathlib import Path
from config.settings import data_path, knowledge_root

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from qfluentwidgets import CardWidget

# U-26: card_layout 统一从 base_page 顶部导入（消除函数内 import 散落）。
from gui.pages.base_page import BasePage, card_layout, card_title

# 攻略库根走「有效知识包」= 可写副本（打包后为 exe 同级的 knowledge/）。
# 关键：guides_loader 会把自定义地图点位（08_custom）动态写进该副本，
# 若这里读只读资源目录，打包版就看不到用户的自定义地图与轨迹。
GUIDES = knowledge_root() / "guides" / "maps"

POINT_FILES = {
    "chests.json": "宝箱", "warptrotters.json": "扑满", "puzzles.json": "解密",
    "books.json": "书籍", "enemies.json": "强敌", "achievements.json": "成就",
    "quests.json": "任务", "shops.json": "商店", "anchors.json": "锚点",
}


class GuidesView(BasePage):
    """攻略体系：左侧大地图 → 右侧区域卡片（点位合并展示，不看细节）。"""

    # 区域执行请求（map_dir, region）——由主窗口接线到知识包执行
    run_requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__("攻略体系", parent)
        self.set_status("选择大地图查看区域")

        split = QSplitter(Qt.Horizontal)
        # 左侧：大地图列表（只到地图层）——35/65 比例，不固定宽（防长名省略号）
        self.map_list = QListWidget()
        self.map_list.setMinimumWidth(180)
        self.map_list.currentItemChanged.connect(self._on_map_selected)
        split.addWidget(self.map_list)

        # 右侧：区域卡片容器（QScrollArea——多区域/多宝箱时不挤在同一屏）
        from PySide6.QtWidgets import QFrame, QScrollArea
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)  # 无边框（与页面风格一致）
        self.region_box = QWidget()
        self.region_layout = QVBoxLayout(self.region_box)
        self.region_layout.setContentsMargins(0, 0, 0, 0)
        self.region_layout.setSpacing(12)
        self.empty_label = QLabel("选择左侧大地图查看区域")
        self.empty_label.setStyleSheet("color: #7A90B0;")
        self.region_layout.addWidget(self.empty_label)
        self.region_layout.addStretch(1)
        self._scroll.setWidget(self.region_box)
        split.addWidget(self._scroll)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([350, 650])
        self.content_layout.addWidget(split, 1)

        self._maps = {}        # map_dir -> {map_name, regions: {region_id: {name, points}}}
        self.reload()

    def reload(self):
        # 刷新防抖（高频触发合并为一次重载）
        from PySide6.QtCore import QTimer
        if getattr(self, "_reload_timer", None) is None:
            self._reload_timer = QTimer(self)
            self._reload_timer.setSingleShot(True)
            self._reload_timer.setInterval(300)
            self._reload_timer.timeout.connect(self._do_reload)
        self._reload_timer.start()

    def _do_reload(self):
        # 记住当前选中地图——重载后恢复选中并重渲染右侧（录制后刷新不跳图）
        cur = self.map_list.currentItem()
        keep = cur.data(Qt.UserRole) if cur is not None else None
        self.map_list.clear()
        self._maps = {}
        total_broken = 0
        if not GUIDES.exists():
            self._set_empty(f"⚠ 攻略库不存在：{GUIDES}（请先导入知识库）")
            return
        for md in sorted(GUIDES.iterdir()):
            if not md.is_dir():
                continue
            broken = []
            try:
                map_doc = json.loads((md / "map.json").read_text(encoding="utf-8"))
            except Exception as e:
                # 韧性：map.json 损坏不再 `continue` 静默跳过——计入 broken 提示
                logging.getLogger("gui.guides_view").warning(
                    "地图 JSON 损坏 %s: %s: %s", md.name,
                    type(e).__name__, e)
                broken.append(f"map.json ({type(e).__name__})")
                total_broken += 1
                continue
            # 区域 → 点位类型计数（合并展示）+ 可执行/待采集区分
            region_pts = defaultdict(lambda: defaultdict(int))
            region_ready = defaultdict(int)   # 真点位（有坐标可执行）
            region_pending = defaultdict(int)  # 骨架（待采集）
            for f, zh in POINT_FILES.items():
                pf = md / "points" / f
                if not pf.exists():
                    continue
                try:
                    pts = json.loads(pf.read_text(encoding="utf-8"))
                except Exception as e:
                    # 韧性：点位 JSON 损坏不再 `continue` 静默跳过——计入 broken
                    logging.getLogger("gui.guides_view").warning(
                        "点位 JSON 损坏 %s/points/%s: %s: %s",
                        md.name, f, type(e).__name__, e)
                    broken.append(f"points/{f} ({type(e).__name__})")
                    total_broken += 1
                    continue
                for pt in pts:
                    region_pts[pt.get("region", "?")][zh] += 1
                    if pt.get("status") == "empty" or pt.get("x") is None:
                        region_pending[pt.get("region", "?")] += 1
                    else:
                        region_ready[pt.get("region", "?")] += 1
            regions = {}
            for a in (md / "areas").glob("*.json"):
                try:
                    adoc = json.loads(a.read_text(encoding="utf-8"))
                except Exception as e:
                    # 韧性：区域 JSON 损坏不再 `continue` 静默跳过——计入 broken
                    logging.getLogger("gui.guides_view").warning(
                        "区域 JSON 损坏 %s/areas/%s: %s: %s",
                        md.name, a.name, type(e).__name__, e)
                    broken.append(f"areas/{a.name} ({type(e).__name__})")
                    total_broken += 1
                    continue
                regions[a.stem] = {"name": adoc.get("name", a.stem),
                                   "points": dict(region_pts.get(a.stem, {})),
                                   "ready": region_ready.get(a.stem, 0),
                                   "pending": region_pending.get(a.stem, 0)}
            total = sum(sum(v.values()) for v in region_pts.values())
            self._maps[md.name] = {"map_name": map_doc.get("name", md.name),
                                   "regions": regions,
                                    "broken": broken}
            label = f"{map_doc.get('name', md.name)}  ({total})"
            if broken:
                label += f"  ⚠ {len(broken)} 项损坏"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, md.name)
            self.map_list.addItem(item)
        if not self.map_list.count():
            # 韧性：所有目录都缺 map.json 或全损坏 → 用户看得见"扫了但都坏了"
            msg = "⚠ 未找到有效地图"
            if total_broken:
                msg += f"（扫描 {total_broken} 项 JSON 损坏已跳过）"
            msg += f" —— {GUIDES}"
            self._set_empty(msg)
            return
        if total_broken:
            logging.getLogger("gui.guides_view").warning(
                "攻略库扫描发现 %d 项 JSON 损坏（详见日志）", total_broken)
        if self.map_list.count():
            # 恢复之前选中的地图（录制/同步后刷新不跳图）；无则回第一张
            target = 0
            if keep:
                for i in range(self.map_list.count()):
                    if self.map_list.item(i).data(Qt.UserRole) == keep:
                        target = i
                        break
            self.map_list.setCurrentRow(target)
            # 强制重渲染右侧（setCurrentRow 相同行不触发信号——
            # "刷新/应用选择"后右侧面板必须重绘，否则看起来无用）
            cur = self.map_list.currentItem()
            if cur is not None:
                self._on_map_selected(cur, None)

    def _set_empty(self, text):
        """空态面板：GUIDES 缺失/空/全损坏时的可见提示（不静默空白）。"""
        self.empty_label = QLabel(text)
        self.empty_label.setStyleSheet("color: #7A90B0; font-size: 13px;")
        self.empty_label.setWordWrap(True)
        self.region_layout.addWidget(self.empty_label)
        self.region_layout.addStretch(1)

    def _on_map_selected(self, cur, _prev):
        # 清空区域容器
        while self.region_layout.count():
            it = self.region_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        if cur is None:
            return
        mdir = cur.data(Qt.UserRole)
        # 自定义地图 → 轨迹管理面板（增删/多选启用）
        if mdir == "08_custom":
            self._render_custom_panel()
            return
        info = self._maps.get(mdir)
        if info is None:
            return
        self.set_status(f"{info['map_name']} — 选择区域开始执行")
        if not info["regions"]:
            self.empty_label = QLabel("该地图暂无区域数据")
            self.empty_label.setStyleSheet("color: #7A90B0;")
            self.region_layout.addWidget(self.empty_label)
            self.region_layout.addStretch(1)
            return
        for rid, rinfo in sorted(info["regions"].items(),
                                  # U-27: 按中文显示名排序（用户视角稳定）——
                                  # 之前按英文 stem 排序（a.stem）顺序无规律。
                                  key=lambda kv: kv[1].get("name", kv[0])):
            card = CardWidget()
            card_title(card, rinfo["name"])
            pts = rinfo["points"]
            desc = "、".join(f"{zh}×{n}" for zh, n in pts.items()) or "无点位"
            # 可执行/待采集区分（骨架空位不可执行——避免"数量对但跑不动"误导）
            ready = rinfo["ready"]
            pending = rinfo["pending"]
            if ready or pending:
                desc += f"（可执行 {ready} / 待采集 {pending}）"
            body = QLabel(f"{desc}")
            body.setStyleSheet("color: #7A90B0; font-size: 13px;")
            row = QHBoxLayout()
            row.addWidget(body)
            row.addStretch(1)
            btn = QPushButton("执行此区域")
            btn.setEnabled(ready > 0)  # 无可执行点位（纯骨架）→ 灰置
            btn.setToolTip("执行该区域已采集（有坐标）的宝箱点位"
                           if ready > 0 else "该区域点位尚未采集（骨架空位）")
            btn.clicked.connect(lambda _=False, r=rid, m=mdir:
                                self._on_run(m, r))
            row.addWidget(btn)
            # U-26: card_layout 走顶部 import（已 in __init__ 顶部）
            card_layout(card).addLayout(row)
            self.region_layout.addWidget(card)
        self.region_layout.addStretch(1)

    def _on_run(self, mdir, region):
        self.run_requested.emit(mdir, region)

    # 自定义地图（轨迹管理）

    def _render_custom_panel(self):
        """自定义地图：轨迹文件管理面板——多选启用为目标 + 增删文件。"""
        # U-26: card_layout / QLabel / QVBoxLayout 已顶部 import；此处
        # 删函数内 import。knowledge.guides_loader 保留在函数内（冷依赖，
        # 顶层 import 会拖慢 guides 页面首次显示；只删本可消除的别名）。
        from knowledge.guides_loader import (sync_custom_map,
                                             custom_enabled_names)
        self.set_status("自定义 — 勾选启用为目标（指挥台可见），可删除轨迹")
        enabled = custom_enabled_names()

        card = CardWidget()
        card_title(card, "轨迹文件（勾选 = 作为目标展示）")
        # 复用统一布局（card_title 已初始化——切勿再 new QVBoxLayout 替换）
        v = card_layout(card)

        traj_dir = data_path("knowledge") / "trajectories"
        files = sorted(traj_dir.glob("*.json")) if traj_dir.exists() else []
        if not files:
            v.addWidget(QLabel("暂无轨迹——先到出击页点「● 录制轨迹」录一段"))
            self.region_layout.addWidget(card)
            self.region_layout.addStretch(1)
            return

        # 全选/全不选
        top = QHBoxLayout()
        sel_all = QPushButton("全选")
        sel_none = QPushButton("全不选")
        top.addWidget(sel_all)
        top.addWidget(sel_none)
        top.addStretch(1)
        v.addLayout(top)

        checks = []
        for tf in files:
            cb = QCheckBox(tf.name)
            cb.setChecked(tf.name in enabled)
            checks.append((tf, cb))
            v.addWidget(cb)

        # 底部操作：应用选择 / 删除选中 / 刷新
        bottom = QHBoxLayout()
        apply_btn = QPushButton("应用选择")
        del_btn = QPushButton("删除选中")
        refresh_btn = QPushButton("刷新")
        for b in (apply_btn, del_btn, refresh_btn):
            bottom.addWidget(b)
        bottom.addStretch(1)
        v.addLayout(bottom)
        self.region_layout.addWidget(card)

        def _apply():
            sel = [tf.name for tf, cb in checks if cb.isChecked()]
            n = sync_custom_map(sel)
            self.set_status(f"已应用：{n} 条轨迹作为目标（指挥台已刷新）")
            self.reload()
            # 指挥台联动刷新（目标下拉 + 任务队列）
            try:
                mw = self.window()
                if mw is not None and hasattr(mw, "refresh_command_deck"):
                    mw.refresh_command_deck()
            except Exception:
                pass

        def _delete():
            import os
            targets = [tf for tf, cb in checks if cb.isChecked()]
            if not targets:
                self.set_status("未选择任何轨迹")
                return
            # 二次确认：轨迹文件删除后不可恢复，与同模块 _update_m7/任务启动确认一致
            names = ", ".join(t.name for t in targets[:5])
            if len(targets) > 5:
                names += f" 等 {len(targets)} 个"
            ret = QMessageBox.question(
                self,
                "确认删除轨迹",
                f"将永久删除以下轨迹文件，操作不可撤销：\n{names}\n\n确认继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                return
            removed = 0
            for tf in targets:
                try:
                    os.remove(tf)
                    removed += 1
                except Exception:
                    pass
            if removed:
                # 删除后按剩余勾选同步（修复：原 sync_custom_map() 不带
                # enabled——"应用选择"的启用子集被重置为全部启用）
                sel = [tf.name for tf, cb in checks if cb.isChecked()]
                sync_custom_map(sel)
                self.set_status(f"已删除 {removed} 条轨迹")
                self.reload()
                # 指挥台联动刷新（删除后目标/队列同步移除）
                try:
                    mw = self.window()
                    if mw is not None and hasattr(mw, "refresh_command_deck"):
                        mw.refresh_command_deck()
                except Exception:
                    pass

        def _refresh():
            self.reload()

        def _toggle_all(v):
            for _, cb in checks:
                cb.setChecked(v)

        apply_btn.clicked.connect(_apply)
        del_btn.clicked.connect(_delete)
        refresh_btn.clicked.connect(_refresh)
        sel_all.clicked.connect(lambda: _toggle_all(True))
        sel_none.clicked.connect(lambda: _toggle_all(False))
        self.region_layout.addStretch(1)
