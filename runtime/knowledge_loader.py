import hashlib
import json
import logging
from pathlib import Path

# 知识包结构版本——不匹配时拒绝加载（防止旧包以错误语义运行）
KNOWLEDGE_SCHEMA_VERSION = 1

# S11：运行器预期的游戏版本（知识包显式声明不同版本时告警，不拒绝
# 游戏更新 UI 变化是渐变过程，版本声明用于人工判断而非硬阻断）
EXPECTED_GAME_VERSION = "2.x"

log = logging.getLogger("runtime.knowledge")

# 扫描/加载时忽略的目录（备份/缓存/临时——防重复数据）
IGNORE_DIRS = {".cache", "backup", "tmp", "cache", "old", "archive"}


class KnowledgeMissingError(FileNotFoundError):
    """知识包文件不存在（加载方区分：缺文件 ≠ 损坏 ≠ schema）。"""


class KnowledgeCorruptError(ValueError):
    """知识包 JSON 损坏（加载方区分：损坏需要重建数据）。"""


class DuplicateIDError(ValueError):
    """加载数据中出现重复 id（区域/点位覆盖风险）。"""


class KnowledgePackage:
    def __init__(self, root: Path, strict_schema=True):
        self.root = Path(root)
        self.meta = self._load("package.json") or {}
        if strict_schema:
            self._check_schema_version()
        self.rooms = self._load("rooms.json")
        # 审查 P1：rooms 为畸形非 dict（如 list）时 `(self.rooms or {}).get`
        # 抛 AttributeError——与 chests 同款防护
        if self.rooms is not None and not isinstance(self.rooms, dict):
            raise KnowledgeCorruptError(
                f"知识包 {self.root.name} 的 rooms.json 应为对象，"
                f"实际 {type(self.rooms).__name__}")
        # 注意：不能 `or []`——空 dict {} 是 falsy 会被吞成空列表（ 误报格式错误）
        self.portals = self._load("portals.json")
        if self.portals is None:
            self.portals = []
        self.landmarks = self._load("landmarks.json")
        if self.landmarks is None:
            self.landmarks = []
        self.chests = self._load("chests.json")
        if self.chests is None:
            self.chests = []
        # 点位加载顺序稳定（仅对 list；非 list 保留原值供 validate 报格式错误）
        if isinstance(self.chests, list):
            self.chests = sorted(self.chests, key=lambda c: str(c.get("id", "")))
        # 区域/点位 id 唯一性（重复 id 会互相覆盖）
        self._check_unique_ids()
        # 运行环境标记（文档约定；代码层不强制 reject，生产调度依赖人工/流程把关）
        self.environment = self.meta.get("environment", "prod")
        self.templates_dir = self.root / "templates"
        self.workflows_dir = self.root / "workflows"
        # 启动即冻结——workflow 缓存 + 内容 hash，运行中改文件不影响执行链
        self._workflow_cache = {}
        self._package_hash = None
        # 数据索引（查询不遍历全表）
        self._index_chests_by_room = {}
        self._index_chests_by_id = {}
        self._index_portals_by_id = {}
        self._index_rooms_by_id = {}
        self._build_indexes()

    def _build_indexes(self):
        """按 room/id 建索引（O(1) 查询替代全表遍历）。"""
        if isinstance(self.chests, list):
            for c in self.chests:
                if isinstance(c, dict) and c.get("id"):
                    self._index_chests_by_id[c["id"]] = c
                    self._index_chests_by_room.setdefault(
                        c.get("room"), []).append(c)
        for p in self.portals or []:
            if isinstance(p, dict) and p.get("id"):
                self._index_portals_by_id[p["id"]] = p
        rooms = (self.rooms or {}).get("rooms", [])
        for r in rooms:
            if isinstance(r, dict) and r.get("id"):
                self._index_rooms_by_id[r["id"]] = r
    def portal_by_id(self, portal_id):
        """O(1) 查询：传送门 by id。"""
        return self._index_portals_by_id.get(portal_id)

    def room_by_id(self, room_id):
        return self._index_rooms_by_id.get(room_id)

    def _check_unique_ids(self):
        seen = {}
        for kind, items in (("rooms", (self.rooms or {}).get("rooms", [])),
                            ("portals", self.portals),
                            ("landmarks", self.landmarks),
                            ("chests", self.chests)):
            for item in items:
                if not isinstance(item, dict):
                    continue
                iid = item.get("id")
                if not iid:
                    continue
                key = f"{kind}:{iid}"
                if key in seen:
                    raise DuplicateIDError(
                        f"知识包 {self.root.name} 中 {kind} id 重复: {iid}")
                seen[key] = True

    def package_hash(self):
        """知识包内容指纹（json 数据 + workflows + 模板文件）。

        -J：Merkle 式——templates/ 是执行依据（阈值匹配对象），漏 hash 会导致
        "模板换了包却看起来没变"；相对路径参与 hash（改名即变指纹）。
        """
        if self._package_hash is not None:
            return self._package_hash
        h = hashlib.sha256()
        for name in ("package.json", "rooms.json", "portals.json",
                     "landmarks.json", "chests.json"):
            p = self.root / name
            if p.exists():
                h.update(name.encode())
                h.update(p.read_bytes())
        for p in sorted(self.workflows_dir.glob("*.json")):
            h.update(p.name.encode())
            h.update(p.read_bytes())
        # 模板目录：递归所有文件（png/jpg/…），相对路径参与指纹
        if self.templates_dir.exists():
            for p in sorted(self.templates_dir.rglob("*")):
                if p.is_file():
                    h.update(str(p.relative_to(self.root)).encode())
                    h.update(p.read_bytes())
        self._package_hash = h.hexdigest()[:12]
        return self._package_hash

    def _check_schema_version(self):
        """schema_version 不匹配 → 拒绝加载并告警（fail-fast）。"""
        ver = self.meta.get("schema_version")
        if ver is None:
            log.warning("知识包 %s 缺 schema_version，按 v%d 解析", self.root.name,
                        KNOWLEDGE_SCHEMA_VERSION)
        elif ver != KNOWLEDGE_SCHEMA_VERSION:
            raise ValueError(
                f"知识包 {self.root.name} schema_version={ver} 不匹配运行器 v{KNOWLEDGE_SCHEMA_VERSION}，"
                f"拒绝加载（更新知识包或运行器）")
        # S11：游戏版本声明不一致 → 告警（游戏 UI 变更时旧模板会失效，留人工判断）
        gv = self.meta.get("game_version")
        if gv and gv != EXPECTED_GAME_VERSION:
            log.warning("知识包 %s 声明 game_version=%s，运行器预期 %s——模板可能过期",
                        self.root.name, gv, EXPECTED_GAME_VERSION)

    def _load(self, name):
        p = self.root / name
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            # JSON 损坏明确归类（不是"空包"）
            raise KnowledgeCorruptError(
                f"知识包 {self.root.name} 的 {name} 损坏: {e}") from e
        except OSError as e:
            raise KnowledgeMissingError(
                f"知识包 {self.root.name} 的 {name} 读取失败: {e}") from e

    def workflow(self, target_id):
        # 缓存冻结——执行链不感知运行期文件修改
        if target_id in self._workflow_cache:
            return self._workflow_cache[target_id]
        p = self.workflows_dir / f"{target_id}.json"
        if not p.exists():
            return None
        try:
            wf = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise KnowledgeCorruptError(
                f"知识包 {self.root.name} 的 workflow {target_id} 损坏: {e}") from e
        except OSError as e:
            raise KnowledgeMissingError(
                f"知识包 {self.root.name} 的 workflow {target_id} 读取失败: {e}") from e
        self._workflow_cache[target_id] = wf
        return wf

    def verify_expectations(self, target_id):
        """Sprint B.2：目标双验证表达——workflow.verify 步骤声明的视觉前置。

        返回 {"ocr": [...]}（未声明 → 缺字段）。
        供观察通道在动作前做画面文字校验（由知识包声明，非代码硬编码）。
        """
        wf = self.workflow(target_id)
        if not wf:
            return {}
        for step in (wf.get("steps") or []):
            if step.get("type") == "verify":
                out = {}
                ocr = step.get("ocr")
                if isinstance(ocr, list) and ocr:
                    out["ocr"] = [str(k) for k in ocr]
                elif isinstance(ocr, dict):
                    # must/forbid/context 表达（"商店"+"商品" 且无 "关闭"）
                    out["ocr"] = {"must": [str(k) for k in ocr.get("must", [])],
                                  "forbid": [str(k) for k in ocr.get("forbid", [])],
                                  "context": [str(k) for k in ocr.get("context", [])]}
                return out
        return {}

    def spawn_room(self):
        return self.rooms["spawn_room"] if self.rooms else None

    def room_ids(self):
        return {r["id"] for r in self.rooms["rooms"]} if self.rooms else set()

    def chest(self, chest_id):
        return self._index_chests_by_id.get(chest_id)

    def entity_position(self, entity_id):
        """实体固定点位坐标（chests.json 的 x/y 归一化坐标）。

        借鉴 Local 路线机制：界面图匹配 + 固定相对坐标点击——视频预处理
        已把宝箱位置归一化入库（0-1），模板匹配失败时按点位坐标兜底点击。
        无坐标（如 chest_A 纯模板目标）→ None。
        """
        c = self._index_chests_by_id.get(entity_id)
        if not c:
            return None
        x, y = c.get("x"), c.get("y")
        if x is None or y is None:
            return None
        # 只认归一化坐标（0-1）——absolute/未知类型拒绝兜底（防坐标语义错乱）
        if c.get("coordinate_type", "normalized") != "normalized":
            return None
        try:
            return (float(x), float(y))
        except (TypeError, ValueError):
            return None

    def portal(self, portal_id):
        return self._index_portals_by_id.get(portal_id)

    def template_exists(self, name):
        return (self.templates_dir / name).exists()

    def entity_templates(self):
        """实体→模板映射：扫描 workflows/*.json 的 interact 步骤，
        并合并 portals/landmarks 的 trigger 模板。
        entity_id（世界实体）→ 模板文件名（driver/executor 层解析用）。"""
        mapping = {}
        if self.workflows_dir.exists():
            for p in sorted(self.workflows_dir.glob("*.json")):
                try:
                    wf = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                tid = wf.get("target_id")
                for step in wf.get("steps", []):
                    if step.get("type") == "interact" and step.get("template"):
                        mapping[tid] = step["template"]
                        break
        for item in (self.portals or []) + (self.landmarks or []):
            trig = item.get("trigger") or {}
            tmpl = trig.get("template") or item.get("template")
            if item.get("id") and tmpl:
                mapping.setdefault(item["id"], tmpl)
        return mapping
