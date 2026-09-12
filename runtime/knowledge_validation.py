"""runtime.knowledge_validation — 知识包图校验（canonical 实现）。

DS 审计 V3 解环：从 ingest/compiler/validate_graph.py 迁入
校验逻辑只依赖 runtime.knowledge_loader.KnowledgePackage（同层依赖，
无跨层边）。ingest 侧保留薄转发（向后兼容旧 import 路径 +
`python -m ingest.compiler.validate_graph` CLI 入口）。

白名单与执行器 STEP_TYPES（_orchestrator_core.py）对齐
portal 已由执行器支持（地图传送/门传送），可放行；未知类型会放行必然
失败的 workflow。
"""
import json
import sys
from pathlib import Path

from runtime.knowledge_loader import KnowledgePackage

ALLOWED_STEP_TYPES = {"move", "interact", "verify", "portal", "trajectory"}

# F2 修复：move 步骤的合法 direction 白名单（与 map_converter MOVE_KEYS 对齐）
# 非 wasd direction 的 move 步骤（如 await/view_reset/r/space 等）无法被
# orchestrator 的 move handler 执行，应标记为 warning。
ALLOWED_MOVE_DIRECTIONS = {"w", "a", "s", "d"}


def validate(pkg: KnowledgePackage, verbose=True):
    errors = []
    warnings = []

    if pkg.rooms is None:
        errors.append("缺少 rooms.json")
    else:
        if "spawn_room" not in pkg.rooms:
            errors.append("rooms.json 缺少 spawn_room")
        rooms = pkg.rooms.get("rooms", [])
        if not rooms:
            errors.append("rooms.json 的 rooms 为空")
        for r in rooms:
            if "id" not in r:
                errors.append("rooms.json 中存在缺少 id 的房间")

    if pkg.chests is None:
        errors.append("缺少 chests.json")
    elif not isinstance(pkg.chests, list):
        # chests 非 list（如 {} 或字符串）→ 明确格式错误而非遍历 key
        errors.append(f"chests.json 格式错误（应为列表，实际 {type(pkg.chests).__name__}）")
    else:
        room_ids = pkg.room_ids()
        baseline_resolutions = {}    # tuple -> [cid, ...]
        for c in pkg.chests:
            cid = c.get("id")
            if not cid:
                errors.append("chests.json 中存在缺少 id 的宝箱")
                continue
            has_xy = False
            for field in ("x", "y"):
                if field not in c or c[field] is None:
                    warnings.append(f"宝箱 {cid} 无 {field} 坐标（mock/逻辑测试点位）")
                    continue
                has_xy = True
                try:
                    v = float(c[field])
                    if not (0.0 <= v <= 1.0):
                        errors.append(f"宝箱 {cid} 的 {field}={v} 超出归一化范围 [0,1]")
                except (TypeError, ValueError):
                    errors.append(f"宝箱 {cid} 的 {field} 非数值: {c[field]!r}")
            if has_xy:
                ct = c.get("coordinate_type")
                if ct not in (None, "normalized"):
                    errors.append(
                        f"宝箱 {cid} 的 coordinate_type={ct!r} 与归一化坐标 x/y 不一致"
                        f"（仅 normalized 接受兜底；absolute 拒绝）")
                elif ct is None:
                    warnings.append(
                        f"宝箱 {cid} 有 x/y 坐标但未声明 coordinate_type（默认 normalized）")
                res = c.get("resolution")
                if res is None:
                    warnings.append(
                        f"宝箱 {cid} 有 x/y 坐标但未声明 resolution（建议 [w,h]）")
                else:
                    key = tuple(res) if isinstance(res, list) else None
                    if key:
                        baseline_resolutions.setdefault(key, []).append(cid)
            if c.get("room") not in room_ids:
                errors.append(f"宝箱 {cid} 的 room '{c.get('room')}' 不存在")
            if c.get("template") and not pkg.template_exists(c["template"]):
                errors.append(f"宝箱 {cid} 的模板图片缺失: templates/{c['template']}")
            wf = pkg.workflow(cid)
            if wf is None:
                errors.append(f"宝箱 {cid} 缺少 workflow: workflows/{cid}.json")
            elif wf.get("protocol") != "1.3":
                errors.append(f"宝箱 {cid} 的 workflow 协议版本应为 1.3")
        if len(baseline_resolutions) > 1:
            lines = []
            for res, ids in sorted(baseline_resolutions.items(),
                                    key=lambda kv: -len(kv[1])):
                lines.append(f"{list(res)}({len(ids)}条): {','.join(ids[:3])}"
                             f"{'...' if len(ids) > 3 else ''}")
            warnings.append(
                "归一化坐标基准不一致（多套 resolution 混用——可能来自不同真机录制，"
                "entity_position 兜底可能错位）：\n  " + "\n  ".join(lines))

    room_ids = pkg.room_ids()
    for p in pkg.portals or []:
        pid = p.get("id")
        if not pid:
            errors.append("portals.json 中存在缺少 id 的传送门")
            continue
        if p.get("from") not in room_ids:
            errors.append(f"传送门 {pid} 的 from '{p.get('from')}' 不存在")
        if p.get("to") not in room_ids:
            errors.append(f"传送门 {pid} 的 to '{p.get('to')}' 不存在")
        if p.get("trigger", {}).get("template") and not pkg.template_exists(p["trigger"]["template"]):
            errors.append(f"传送门 {pid} 的触发模板缺失: templates/{p['trigger']['template']}")

    spawn = pkg.spawn_room()
    if room_ids and spawn and pkg.portals:
        adj = {}
        for p in pkg.portals or []:
            adj.setdefault(p.get("from"), set()).add(p.get("to"))
        reachable = set()
        stack = [spawn] if spawn in room_ids else []
        while stack:
            r = stack.pop()
            if r in reachable:
                continue
            reachable.add(r)
            stack.extend(adj.get(r, set()) - reachable)
        graph_nodes = set(adj)
        unreachable = graph_nodes - reachable
        for r in sorted(unreachable):
            errors.append(f"房间 {r} 不可达（从出生点 {spawn} 无路径）")
        for c in pkg.chests or []:
            if isinstance(c, dict) and c.get("room") in unreachable:
                errors.append(f"宝箱 {c.get('id')} 位于不可达房间 {c.get('room')}")

    for landmark in pkg.landmarks or []:
        if landmark.get("room") not in room_ids:
            errors.append(f"地标 {landmark.get('id')} 的 room '{landmark.get('room')}' 不存在")
        if landmark.get("template") and not pkg.template_exists(landmark["template"]):
            errors.append(f"地标 {landmark.get('id')} 的模板图片缺失: templates/{landmark['template']}")

    for c in pkg.chests or []:
        wf = pkg.workflow(c["id"]) if isinstance(c, dict) and c.get("id") else None
        if not wf:
            continue
        for i, step in enumerate(wf.get("steps", [])):
            if not isinstance(step, dict):
                errors.append(f"{c.get('id')} workflow 第 {i} 步不是对象: {type(step).__name__}")
                continue
            if step.get("type") not in ALLOWED_STEP_TYPES:
                errors.append(f"{c['id']} workflow 第 {i} 步类型非法: {step.get('type')}")
            # F2 修复：move 步骤的 direction 字段检查——非 wasd 方向 warning 级
            if step.get("type") == "move":
                direction = step.get("direction")
                if direction and direction not in ALLOWED_MOVE_DIRECTIONS:
                    warnings.append(
                        f"{c['id']} 第 {i} 步 move direction={direction!r} 非 wasd"
                        f"（orchestrator 无法执行，map_converter 已修复不再生成）")
            if step.get("template") and not pkg.template_exists(step["template"]):
                errors.append(
                    f"{c['id']} 第 {i} 步模板缺失: templates/{step['template']}")
            if step.get("type") == "verify":
                sig = step.get("signal")
                if sig and not pkg.template_exists(f"{sig}.png"):
                    errors.append(
                        f"{c['id']} verify 信号模板缺失: templates/{sig}.png")
            if step.get("type") == "portal":
                pid = step.get("portal_id")
                if not pid or pkg.portal(pid) is None:
                    errors.append(
                        f"{c['id']} workflow 引用不存在的传送门: {pid}")
                else:
                    portal = pkg.portal(step["portal_id"])
                    if portal and portal.get("to") != c.get("room"):
                        warnings.append(f"{c['id']} 经传送门 {step['portal_id']} 到达 {portal.get('to')}，但宝箱在 {c.get('room')}")

    if pkg.workflows_dir.exists():
        chest_ids = {c.get("id") for c in (pkg.chests or [])
                     if isinstance(c, dict)}
        for wf_file in sorted(pkg.workflows_dir.glob("*.json")):
            try:
                wf = json.loads(wf_file.read_text(encoding="utf-8"))
            except Exception as e:
                errors.append(f"workflow 损坏: {wf_file.name} ({type(e).__name__})")
                continue
            tid = wf.get("target_id") if isinstance(wf, dict) else None
            if tid is not None and tid not in chest_ids:
                errors.append(
                    f"孤儿 workflow {wf_file.name}: target_id={tid} 不在 chests 注册表")

    if verbose:
        for w in warnings:
            print(f"[WARN] {w}")
        for e in errors:
            print(f"[ERROR] {e}")
        print(f"== validate: {len(errors)} error(s), {len(warnings)} warning(s) ==")
    return errors, warnings


def main():
    if len(sys.argv) < 2:
        print("用法: python -m runtime.knowledge_validation <knowledge_dir>")
        sys.exit(2)
    pkg = KnowledgePackage(Path(sys.argv[1]))
    errors, _ = validate(pkg)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
