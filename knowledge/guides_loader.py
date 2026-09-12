"""guides 攻略数据加载（GUI 联动：指挥台目标 = 攻略存档真实点位）。

knowledge/guides/maps/<map_dir>/points/*.json → 目标列表。
"""
import json
import json as _json
import logging
from pathlib import Path

# 可写知识副本根：knowledge/ 虽为随包分发的只读资源，但本模块要动态写入
# 自定义地图点位（08_custom），故走 DATA_ROOT 的可写副本（源码模式下同仓库根）。
from config.settings import data_path as _data_path

log = logging.getLogger(__name__)

GUIDES = _data_path("knowledge") / "guides" / "maps"

# 自定义地图（录制轨迹平级展示）：08_custom 点位动态同步自 trajectories/
CUSTOM_MAP = "08_custom"


# 占位轨迹元信息标记词（t9 F2）：source/description 命中任一 → 判占位。
# 依据：knowledge/trajectories/base_zone_run.json 的 source 字段自述
# 「占位轨迹，实际录制覆盖」，含 10 事件其中 1 次 click。fresh clone 场景下
# sync_custom_map(None) 会全量启用所有 trajectories/*.json（见 gui/run.py:273-278
# 的 `enabled or None` 兜底），占位轨迹若无过滤会被当作真实目标注册进
# 08_custom，进而被 orchestrator 回放（其中含 1 次 SendInput click）。
_PLACEHOLDER_MARKERS = ("占位", "placeholder", "stub", "example")
# 占位轨迹事件数下限：低于此值判定为占位或损坏（真实录制通常 50+ 事件）。
# 阈值定在 10 而非 5：base_zone_run.json 的 10 事件正是本缺陷的确证样本；
# 定在 5 会让 5-9 事件的正常微操作轨迹被误伤。
_PLACEHOLDER_MAX_EVENTS = 10


def _is_placeholder_trajectory(tf: Path) -> bool:
    """判定轨迹 JSON 是否为占位样本，禁止被当作可执行目标注册。

    两条判据（任一命中即判占位，均不修改数据文件本体）：
    1. source/description 元字段命中占位标记词——设计者自述，权威信号。
    2. events 数低于 _PLACEHOLDER_MAX_EVENTS——区分占位与损坏文件。

    JSON 解析异常保守回 True（当作占位）：无法读到的文件不应进入执行链。
    """
    try:
        data = _json.loads(tf.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("轨迹文件读取失败，按占位处理不注册: %s (%s)", tf.name, e)
        return True
    if not isinstance(data, dict):
        return True
    meta = " ".join(str(data.get(k) or "") for k in ("source", "description"))
    if any(m in meta.lower() for m in _PLACEHOLDER_MARKERS):
        return True
    events = data.get("events")
    return not isinstance(events, list) or len(events) < _PLACEHOLDER_MAX_EVENTS


# 骨架点位判据（t33 D1）：无坐标或 status=empty → 不可执行目标，不进入可执行集。
# 依据：knowledge/guides/maps/ 共 1504 点位中 1474 为 x/y=null 的骨架占位
# （见 docs/audit/stage4_module_analysis.md 逐图分布），若不过滤则全部
# 进指挥台目标树——用户勾选后无法执行（无坐标、无轨迹）。
# 判据用 x/y 是否为 null（不是 0，因为归一化后 0 是合法坐标）：
#   - herta 52 点位中 30 有坐标（真点位）+ 22 无坐标（骨架）
#   - 其它 5 张地图全部为骨架（x/y=null）
#   - 08_custom 只由轨迹同步而来，点位自带 trajectory 字段，无 x/y 也无妨
#     —— 走 trajectory 执行链，不走坐标点击链。
def _is_skeleton_point(it: dict) -> bool:
    """判定攻略点位是否为骨架占位（无坐标且无轨迹）。

    判据（任一命中即判骨架）：
    1. x/y 任一为 None（JSON null）——无坐标，无法执行坐标点击；
    2. x/y 缺失——同上（.get() 返回 None）；
    3. status 为 "empty" / "skeleton" / "placeholder"——显式标记的占位。

    例外：含 trajectory 字段的点位不判骨架（08_custom 走轨迹执行链，
    无坐标是正常形态）。
    """
    if it.get("trajectory"):
        return False
    if it.get("x") is None or it.get("y") is None:
        return True
    status = str(it.get("status") or "").lower()
    return status in ("empty", "skeleton", "placeholder")


def sync_custom_map(enabled=None):
    """同步录制轨迹 → 自定义地图点位（世界图/指挥台自动平级展示）。

    每个轨迹文件 = 一个点位（id=文件名去扩展名，trajectory 指向原文件）。
    enabled: 可选启用集（轨迹文件名列表）——None=全部启用；
    未启用的轨迹文件保留在 trajectories/ 但不作为目标展示。
    录制保存后调用即可——两边读同一 GUIDES 目录，动态一致。

    过滤规则（t9 F2）：占位轨迹（元信息命中占位标记词或事件数 < 10）
    一律跳过不注册——即使 enabled=None 全量启用也不例外。这一层是占位
    数据进入目标集合的唯一入口；不改数据文件本体，也不改 orchestrator。
    """
    traj_dir = _data_path("knowledge") / "trajectories"
    md = GUIDES / CUSTOM_MAP
    (md / "points").mkdir(parents=True, exist_ok=True)
    (md / "areas").mkdir(parents=True, exist_ok=True)
    # 区域文件（数据完整性校验需要——自定义地图统一"自定义"区域）
    (md / "areas" / "custom.json").write_text(
        _json.dumps({"name": "自定义", "id": "custom"},
                    ensure_ascii=False, indent=1), encoding="utf-8")
    (md / "map.json").write_text(
        _json.dumps({"id": CUSTOM_MAP, "name": "自定义"},
                    ensure_ascii=False, indent=1), encoding="utf-8")
    items = []
    if traj_dir.exists():
        for tf in sorted(traj_dir.glob("*.json")):
            if enabled is not None and tf.name not in enabled:
                continue
            if _is_placeholder_trajectory(tf):
                log.info("跳过占位轨迹不注册为可执行目标: %s", tf.name)
                continue
            items.append({
                "id": tf.stem,
                "name": tf.stem,  # 直接文件名（自定义-1 / traj_xxx），可读可排序
                "region": "custom",  # 区域 id 与其他地图一致（英文 id）
                "type": "chest",
                "trajectory": tf.name,
            })
    (md / "points" / "chests.json").write_text(
        _json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(items)


def custom_enabled_names():
    """当前自定义地图启用的轨迹文件名集合（读 08_custom 点位）。

    返回 None 表示「无显式选择」——点位文件缺失或为空时用它，调用方据此
    按全量启用处理。

    注意（不可返回空集）：启用集本身是从 chests.json 推导的，而 sync_custom_map
    又会写回该文件。若空文件返回空集，sync 会把它当作「用户禁用了全部轨迹」
    并写回空列表——形成自我锁死的循环：一旦为空就永远为空，磁盘上真实存在的
    轨迹再也不会被注册。空文件与缺失文件都代表「尚无选择」，故返回 None。
    """
    import json as _json
    try:
        pf = GUIDES / CUSTOM_MAP / "points" / "chests.json"
        if not pf.exists():
            return None
        pts = _json.loads(pf.read_text(encoding="utf-8"))
        names = {p.get("trajectory") for p in pts if p.get("trajectory")}
        return names or None
    except Exception:
        return None

POINT_TYPES = {
    "chests.json": "chest",
    "warptrotters.json": "warptrotter",
    "puzzles.json": "puzzle",
    "books.json": "book",
    "enemies.json": "enemy",
    "achievements.json": "achievement",
    "quests.json": "quest",
    "shops.json": "shop",
    "anchors.json": "anchor",
}


def load_guide_targets(map_dir="02_herta_space_station", types=None,
                       include_skeleton=False):
    """读攻略存档点位 → 目标列表 [{id, name, region, type, trajectory?}]。

    types=None → 全部；否则白名单（如 ["chest"]）。
    include_skeleton=False（默认）→ 过滤 x/y=null 的骨架占位点位（D1）；
    include_skeleton=True → 保留全部点位（供世界图"全量展示"用，
    不用于指挥台可执行目标集）。
    """
    md = GUIDES / map_dir
    if not md.exists():
        return []
    want = set(types) if types else set(POINT_TYPES.values())
    targets = []
    for fname, ptype in POINT_TYPES.items():
        if ptype not in want:
            continue
        p = md / "points" / fname
        if not p.exists():
            continue
        try:
            items = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("guides_loader: 跳过 %s/%s（JSON 解析失败: %s）",
                        map_dir, fname, e)
            continue
        # 格式非预期（如本应 list 却是 dict）时不再静默丢整张地图——
        # 可见告警 + 跳过该文件、保留其余点位。
        # 原实现直接 `for it in items` 迭代 dict 的 keys（字符串），
        # `it.get()` 抛 AttributeError，被 load_all_targets 外层 except 吞掉
        # → 整张地图静默消失。现改为：先检查 items 类型，非 list 则告警 + continue。
        if not isinstance(items, list):
            log.warning("guides_loader: 跳过 %s/%s（格式非 list，实际 %s）",
                        map_dir, fname, type(items).__name__)
            continue
        for it in items:
            if not isinstance(it, dict):
                log.warning("guides_loader: 跳过 %s/%s 中非 dict 项（%s）",
                            map_dir, fname, type(it).__name__)
                continue
            if not include_skeleton and _is_skeleton_point(it):
                continue
            targets.append({
                "id": it.get("id", ""),
                "name": it.get("name") or it.get("id"),
                "region": it.get("region", ""),
                "type": ptype,
                # 轨迹字段透传（自定义地图点位→指挥台→执行链 trajectory 步骤）
                "trajectory": it.get("trajectory"),
            })
    return targets


def load_guide_regions(map_dir="02_herta_space_station"):
    """区域列表 [{id, name}]（供指挥台展示区域名）。"""
    md = GUIDES / map_dir
    if not md.exists():
        return []
    out = []
    for a in sorted((md / "areas").glob("*.json")):
        try:
            adoc = json.loads(a.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({"id": a.stem, "name": adoc.get("name", a.stem)})
    return out


# S6（S2-B）：从 main_window.refresh_command_deck 抽出"全地图目标聚合 + 同步"
# ——业务下沉到 knowledge/guides_loader，GUI 只消费结果。
def load_all_targets(maps_dir=None, types=None, include_custom=True,
                     include_skeleton=False):
    """聚合所有地图的目标（指挥台"全部目标"使用）。

    返回 [{id, name, region, type, room, map_name, trajectory?}, ...]
    单地图异常不阻断其他地图（与原 main_window 行为一致）。

    maps_dir: 自定义 maps 根（默认 = GUIDES 自身）。
    types:    类型白名单（默认 ["chest"]，与原 main_window 行为一致）。
    include_custom: 是否包含自定义地图（录制轨迹→08_custom）——录制同步
                  流程需要 True，普通启动可 False。
    """
    if types is None:
        types = ["chest"]
    root = Path(maps_dir) if maps_dir is not None else GUIDES
    if not root.is_dir():
        return []
    out = []
    for md in sorted(root.iterdir()):
        if not md.is_dir():
            continue
        map_id = md.name
        if not include_custom and map_id == CUSTOM_MAP:
            continue
        map_display_name = map_id
        try:
            doc = json.loads((md / "map.json").read_text(encoding="utf-8"))
            map_display_name = doc.get("name") or map_id
        except Exception:
            pass
        try:
            ts = load_guide_targets(map_id, types=types,
                                    include_skeleton=include_skeleton)
            regions = {r["id"]: r["name"] for r in load_guide_regions(map_id)}
            for t in ts:
                t["room"] = regions.get(t["region"], t["region"])
                t["map_name"] = map_display_name
            out.extend(ts)
        except Exception:
            continue
    return out


def refresh_all_targets(include_custom=True, include_skeleton=False):
    """S6：录制同步后"刷新全部目标"完整流程。

    原 main_window.refresh_command_deck 的两步组合：
    1. 同步自定义地图（录制轨迹 → 08_custom 点位）——保留用户已勾选启用集
       （无参调用等于全量启用，会把用户关掉的轨迹全部打开，故此处传入启用集）。
    2. 聚合全部目标。
    返回 targets 列表（与原 main_window 同结构）。

    include_skeleton=False（默认）→ 过滤骨架占位（D1 修复）。
    """
    if include_custom:
        sync_custom_map(custom_enabled_names())
    return load_all_targets(include_custom=include_custom,
                            include_skeleton=include_skeleton)
