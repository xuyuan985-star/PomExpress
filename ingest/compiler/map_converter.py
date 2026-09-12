"""地图 JSON → workflow 1.3 转换器。

将 外部参考实现 的 map JSON 格式（start + map 两段式按键脚本）转换为
本项目的 workflow v1.3 格式（protocol/target_id/steps）。

地图 格式回顾：
  start: [{"map":1}, {"picture\\xxx.png":1.5}, ...] 导航/传送模板点击
  map: [{"w":1.66}, {"e":2}, {"fighting":2}, ...] 移动/交互/战斗

workflow v1.3 格式：
  {"protocol":"1.3", "target_id":"...", "steps":[
    {"type":"portal","portal_id":"tp_xxx"},
    {"type":"move","direction":"w","duration":1.66},
    {"type":"interact","key":"e","duration":2},
    {"type":"verify","signal":"...","expected":"vanished","timeout":30}
  ]}

扩展步骤类型（在 ALLOWED_STEP_TYPES 基础上兼容 地图 语义）：
  move 方向移动：{"type":"move","direction":"w|a|s|d","duration":1.66}
  interact 按键交互：{"type":"interact","key":"e|f|fighting","duration":2}
  interact 模板交互：{"type":"interact","template":"map:xxx.png","threshold":0.9}
  portal 地图传送：{"type":"portal","portal_id":"map_auto_<map_name>"}
  verify 验证消失：{"type":"verify","signal":"...","expected":"vanished","timeout":30}

转换器同时生成 portals.json 片段（start 段的模板序列 → map_transfer portal）
和可选 trajectory JSON（map 段的 w/a/s/d → 轨迹回放事件）。
"""
import json
import logging
import re
from pathlib import Path
from typing import Any
from collections import Counter

log = logging.getLogger("ingest.compiler.map_converter")

# 地图 start 段图片路径前缀（m7/3rdparty/外部参考实现/picture/ → assets/map_templates/）
MAP_PICTURE_PREFIX = "picture\\"

# —— 地图 转换器交互阈值与验证超时默认值（可调）——
# 交互阈值（0~1）：模板匹配判为命中所需的最小相似度；地图 传送/交互模板较清晰，
#   故默认较高以抑制误点；取值范围 [0.6, 0.95]，越低越宽松但可能误触发。
# 验证超时（秒）：交互后等待目标图标消失的最长等待；超时视为操作失败。
_DEFAULT_MAP_THRESHOLD = 0.9

# workflow 步骤类型白名单（与 orchestrator STEP_TYPES + validate_graph 对齐）。
# 注：地图 转换器当前不主动产出 trajectory 步骤（trajectory JSON 作为附加产物
# 单独写出，见 convert() 的 trajectory_events 输出），但白名单需与
# _orchestrator_core.STEP_TYPES:26 和 knowledge_validation.ALLOWED_STEP_TYPES
# 保持一致，避免下游误以为 地图 转换产物不支持 trajectory 语义。
STEP_TYPES = {"move", "interact", "verify", "portal", "trajectory"}

# 移动方向键（wasd → 轨迹事件 key）
MOVE_KEYS = {"w", "a", "s", "d"}

# 交互键（e=战技/互动, f=F键互动, fighting=战斗检测）
INTERACT_KEYS = {"e", "f", "fighting"}

# start 段特殊键（非模板点击的特殊动作）
SPECIAL_START_KEYS = {
    "map", "main", "await", "esc", "b", "check", "normal_run",
    "blackscreen", "F4", "space", "floor", "need_allow_map_buy",
    "need_allow_snack_buy", "need_allow_memory_token",
    "need_allow_lost_ember_crystal", "drag", "scene", "clicks",
    "forbid_retry", "allow_skip_f",
}

# map 段特殊键（非移动/非交互的特殊动作）
SPECIAL_MAP_KEYS = {
    "space", "caps", "r", "mouse_move", "scroll", "view_set",
    "view_reset", "view_rotate", "esc", "await", "main",
    "check", "shutdown", "allow_skip_f",
    "1", "2", "3", "4", "5",  # 角色切换
}

# 机关检查模板前缀（匹诺康尼筑梦机关）
MECHANISM_CHECK_PREFIX = "picture\\check_4-1_point"


class 地图MapData:
    """解析后的 地图数据（name/author/start/map）。"""

    def __init__(self, raw: dict):
        self.name: str = raw.get("name", "")
        self.author: str = raw.get("author", "")
        self.start: list = raw.get("start", [])
        self.map: list = raw.get("map", [])
        self.raw = raw

    @property
    def map_key(self) -> str:
        """从 name 提取地图编号（如 "基座舱段-1" → "1" 段无编号；
        文件名 map_1-1_0.json 的 key 是 "1-1_0"）。"""
        # name 格式 "区域名-N"，N 是子图编号
        # 实际地图编号从文件名获取更可靠
        return self.name

    @classmethod
    def from_file(cls, path: Path) -> "地图MapData":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data)


def _extract_key_value(action: dict) -> tuple[str, Any]:
    """从单键 dict 提取 (key, value)。地图数据每个元素是 {key: value}。"""
    if not isinstance(action, dict) or len(action) != 1:
        return ("", None)
    k, v = next(iter(action.items()))
    return (k, v)


def _is_picture_key(key: str) -> bool:
    """判断 key 是否是图片模板路径（picture\\xxx.png）。"""
    return key.startswith(MAP_PICTURE_PREFIX) or key.startswith("picture/")


def _picture_to_map_template(key: str) -> str:
    """将 picture\\xxx.png 转为 map:xxx.png 前缀引用。"""
    # 去掉 picture\\ 或 picture/ 前缀
    name = key.replace("picture\\", "").replace("picture/", "")
    return f"map:{name}"


def _map_name_to_id(name: str) -> str:
    """将地图名转为合法 id（中文→拼音不可靠，用原始 name 做可读 id）。"""
    # 保留原始 name 作为可读 id；workflow target_id 用文件名
    # 去掉空格和特殊字符
    safe = re.sub(r"[^\w\u4e00-\u9fff\-]", "_", name)
    return safe


class 地图Converter:
    """地图 JSON → workflow 1.3 转换器。

    用法：
        conv = 地图Converter()
        result = conv.convert_map_file(Path("map_1-1_0.json"))
        # result.workflow → workflow 1.3 dict
        # result.portal_fragment → portals.json 片段（start 段模板序列）
        # result.trajectory_events → 轨迹事件列表（map 段移动键）
    """

    def __init__(self, default_threshold: float = _DEFAULT_MAP_THRESHOLD,
                 default_verify_timeout: int = 30):
        self.default_threshold = default_threshold
        self.default_verify_timeout = default_verify_timeout

    def convert_map_file(self, path: Path) -> "地图ConversionResult":
        """转换单个 地图 JSON 文件。

        Args:
            path: 地图 JSON 文件路径（如 map_1-1_0.json）

        Returns:
            地图ConversionResult: 包含 workflow, portal_fragment, trajectory_events
        """
        data = 地图MapData.from_file(path)
        # 从文件名提取地图编号（map_1-1_0.json → "1-1_0"）
        stem = path.stem  # "map_1-1_0"
        map_id = stem.replace("map_", "", 1) if stem.startswith("map_") else stem
        target_id = f"map_{map_id}"

        return self.convert_map_data(data, map_id, target_id)

    def convert_map_data(self, data: 地图MapData, map_id: str,
                         target_id: str | None = None) -> "地图ConversionResult":
        """转换 地图MapData → workflow 1.3。

        Args:
            data: 解析后的 地图数据
            map_id: 地图编号（如 "1-1_0"）
            target_id: workflow target_id（默认 map_<map_id>）
        """
        if target_id is None:
            target_id = f"map_{map_id}"

        start_discarded_keys: Counter = Counter()
        map_non_wasd_counts: Counter = Counter()

        portal_fragment = self._convert_start(data, map_id, target_id,
                                              start_discarded_keys)
        trajectory_events = []
        steps = self._convert_map(data, map_id, trajectory_events,
                                  map_non_wasd_counts)

        # 日志汇总：start 段丢弃
        if start_discarded_keys:
            total_discarded = sum(start_discarded_keys.values())
            detail = ", ".join(f"{k}={v}" for k, v in
                               sorted(start_discarded_keys.items()))
            log.warning(
                "[%s] start 段丢弃 %d 个未识别 key: %s",
                map_id, total_discarded, detail)

        # 日志汇总：map 段非 wasd direction 跳过
        if map_non_wasd_counts:
            total_skipped = sum(map_non_wasd_counts.values())
            detail = ", ".join(f"{k}={v}" for k, v in
                               sorted(map_non_wasd_counts.items()))
            log.warning(
                "[%s] map 段跳过 %d 个非 wasd direction key: %s",
                map_id, total_skipped, detail)

        # 组装 workflow
        all_steps = []
        # 1. portal 步骤（start 段 → 地图传送）
        if portal_fragment:
            all_steps.append({
                "type": "portal",
                "portal_id": portal_fragment["id"],
            })
        # 2. map 段步骤（移动 + 交互）
        all_steps.extend(steps)

        workflow = {
            "protocol": "1.3",
            "target_id": target_id,
            "map_name": data.name,
            "map_id": map_id,
            "author": data.author,
            "steps": all_steps,
        }

        return 地图ConversionResult(
            workflow=workflow,
            portal_fragment=portal_fragment,
            trajectory_events=trajectory_events,
            map_id=map_id,
            map_name=data.name,
        )

    def _convert_start(self, data: 地图MapData, map_id: str,
                       target_id: str,
                       start_discarded_keys: Counter | None = None) -> dict | None:
        """转换 start 段 → portal 片段（map_transfer 类型）。

        start 段是导航/传送模板点击序列，转换为 portals.json 中的
        map_transfer 类型 portal（steps 模板序列 + load_wait）。

        特殊键（map/main/await/esc 等）不进入模板序列——它们是
        导航辅助动作，在 portal 步骤执行前后由执行器隐含处理。
        WASD 等未识别键不再静默丢弃——记入 start_discarded_keys 并 warning。
        """
        template_steps = []
        has_transfer = False

        for action in data.start:
            key, value = _extract_key_value(action)
            if not key:
                continue

            if key == "map":
                # 打开地图——portal 步骤隐含（执行器先按 M 打开地图）
                continue
            elif key == "main":
                # 回主界面——portal 前的预备动作
                continue
            elif key in SPECIAL_START_KEYS:
                # 其他特殊键不进入模板序列
                continue
            elif _is_picture_key(key):
                tpl = _picture_to_map_template(key)
                threshold = self.default_threshold
                # transfer.png 是传送确认——标记为有传送
                if "transfer" in key.lower():
                    has_transfer = True
                template_steps.append({
                    "template": tpl,
                    "threshold": threshold,
                })
            else:
                # F1 修复：不再静默丢弃——记入 Counter 并 warning
                if start_discarded_keys is not None:
                    start_discarded_keys[key] += 1
                log.warning(
                    "start 段丢弃未识别 key=%r (value=%s)——非 picture/非特殊键，"
                    "未进入 portal 模板序列。如需保留请扩展 _convert_start 逻辑。",
                    key, value)

        if not template_steps:
            return None

        portal_id = f"tp_map_{map_id}"
        return {
            "id": portal_id,
            "from": "spawn",  # 起始点（通用）
            "to": f"map_{map_id}",  # 目标区域
            "kind": "map_transfer",
            "steps": template_steps,
            "load_wait": 10,  # 传送后等待地图加载
        }

    def _convert_map(self, data: 地图MapData, map_id: str,
                     trajectory_events: list,
                     map_non_wasd_counts: Counter | None = None) -> list:
        """转换 map 段 → workflow 步骤列表。

        map 段按键类型：
          w/a/s/d → move（方向移动，duration 秒）+ 轨迹事件
          e/f/fighting → interact（交互/战斗，duration 秒）
          space/r/esc/caps/1-5 → 跳过 + warning（orchestrator
            的 _step_interact 只做模板匹配、不读 key 字段，生成带 key 的
            interact 步骤会误导——归入 SKIP_KEYS 并计数汇总）
          view_set/view_reset/view_rotate/await/main/check/shutdown/
          allow_skip_f/mouse_move/scroll → 跳过（相机/元动作，无对应步骤语义）
          其他未识别 → 跳过 + warning（不再静默丢弃）
        """
        steps = []
        last_interact = False

        # EXTENDED_INTERACT_KEYS 归入 SKIP_KEYS
        # 原因：orchestrator 的 _step_interact 只做模板匹配、不读 key 字段，
        # 生成带 key 的 interact 步骤会打上错误语义标签（跳跃/重试/退出/切换角色
        # 被当成"找宝箱模板并点击"）。诚实跳过 + Counter 汇总（与 F1 的
        # start_discarded_keys 同款做法）。功能级按键执行路径需用户决策后再实现。
        SKIP_KEYS = {"view_set", "view_reset", "view_rotate",
                      "await", "main", "check", "shutdown",
                      "allow_skip_f", "mouse_move", "scroll",
                      "space", "r", "esc", "caps",
                      "1", "2", "3", "4", "5"}


        for action in data.map:
            key, value = _extract_key_value(action)
            if not key:
                continue

            # 确保 value 是数值
            duration = 0.0
            if isinstance(value, (int, float)):
                duration = float(value)
            elif isinstance(value, list):
                # floor 等特殊值是 [arr, target] —— 跳过
                continue

            if key in MOVE_KEYS:
                # 方向移动 → move 步骤 + 轨迹事件
                steps.append({
                    "type": "move",
                    "direction": key,
                    "duration": duration,
                })
                # 生成轨迹事件（key down → sleep → key up）
                self._append_trajectory_key(trajectory_events, key, duration)
                last_interact = False

            elif key in INTERACT_KEYS:
                # 交互/战斗 → interact 步骤
                interact_step = {
                    "type": "interact",
                    "key": key,
                    "duration": duration,
                }
                if key == "fighting":
                    interact_step["combat"] = True
                elif key == "e":
                    interact_step["skill"] = True
                elif key == "f":
                    interact_step["f_key"] = True
                steps.append(interact_step)
                last_interact = True


            elif key in SKIP_KEYS:
                # F2 修复 + 相机/元动作/扩展交互键 → 跳过 + 计数
                if map_non_wasd_counts is not None:
                    map_non_wasd_counts[key] += 1
                log.warning(
                    "[%s] map 段跳过 key=%r（无对应 workflow 步骤语义——"
                    "orchestrator 的 _step_interact 不读 key 字段）",
                    map_id, key)
                last_interact = False

            elif key in SPECIAL_MAP_KEYS:
                # F2 修复：剩余 SPECIAL_MAP_KEYS（理论上已被上方分支覆盖）
                # 保守处理：跳过 + 计数 + warning
                if map_non_wasd_counts is not None:
                    map_non_wasd_counts[key] += 1
                log.warning(
                    "[%s] map 段跳过 key=%r（SPECIAL_MAP_KEYS 未映射）",
                    map_id, key)
                last_interact = False

            else:
                # F2 修复：未识别键 → 跳过 + 计数 + warning（不再静默丢弃）
                if map_non_wasd_counts is not None:
                    map_non_wasd_counts[key] += 1
                log.warning(
                    "[%s] map 段跳过未识别 key=%r, value=%r"
                    "——不再生成 move+非wasd direction（orchestrator 无法执行）",
                    map_id, key, value)
                last_interact = False

        # 末尾追加 verify 步骤（如果最后是交互/战斗 → 验证宝箱消失）
        if last_interact:
            steps.append({
                "type": "verify",
                "signal": "minimap_chest_icon",
                "expected": "vanished",
                "timeout": self.default_verify_timeout,
                "ocr": ["宝箱", "任务", "委托"],
            })

        return steps

    def _append_trajectory_key(self, events: list, key: str,
                               duration: float):
        """生成轨迹回放事件（key down → time_sleep → key up）。

        与 TrajectoryReplayer 的事件格式对齐：
          {"key": "w", "time_sleep": 0} # key down
          {"time_sleep": 1.66} # 等待
          # key up 由下一个 key 事件或末尾隐含
        """
        # key down 事件
        events.append({"key": key, "time_sleep": 0})
        # 等待 duration 秒
        if duration > 0:
            events.append({"time_sleep": duration})
        # key up 事件（用 key + duration=0 表示释放）
        events.append({"key": key, "time_sleep": 0, "release": True})

    def convert_directory(self, map_dir: Path,
                          output_dir: Path | None = None) -> list:
        """批量转换目录下所有 地图 JSON。

        Args:
            map_dir: 地图目录（如 .../外部参考实现/map/HuangQuan/）
            output_dir: 输出目录（默认 map_dir / "workflow_output"）

        Returns:
            [地图ConversionResult, ...] 列表
        """
        if output_dir is None:
            output_dir = map_dir / "workflow_output"
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        results = []
        portals = []
        success_count = 0
        fail_count = 0
        fail_details: list[str] = []
        for jf in sorted(map_dir.glob("map_*.json")):
            try:
                result = self.convert_map_file(jf)
                results.append(result)
                if result.portal_fragment:
                    portals.append(result.portal_fragment)
                # 写 workflow JSON
                wf_path = output_dir / f"{result.map_id}.json"
                wf_path.write_text(
                    json.dumps(result.workflow, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                success_count += 1
            except Exception as e:
                fail_count += 1
                fail_details.append(f"{jf.name}: {type(e).__name__}: {e}")
                log.error("转换 %s 失败: %s", jf.name, e)

        # F3 修复：汇总报告——成功/失败计数 + 失败明细
        total = success_count + fail_count
        log.info(
            "转换完成: 成功 %d/%d 个地图, %d 个传送门",
            success_count, total, len(portals))
        if fail_count > 0:
            log.warning(
                "转换汇总: %d/%d 个地图失败——请检查以下文件:\n  %s",
                fail_count, total, "\n  ".join(fail_details))

        # 写合并的 portals.json
        if portals:
            (output_dir / "portals.json").write_text(
                json.dumps(portals, ensure_ascii=False, indent=2),
                encoding="utf-8")

        return results


class 地图ConversionResult:
    """单次转换结果。"""

    def __init__(self, workflow: dict, portal_fragment: dict | None,
                 trajectory_events: list, map_id: str, map_name: str):
        self.workflow = workflow
        self.portal_fragment = portal_fragment
        self.trajectory_events = trajectory_events
        self.map_id = map_id
        self.map_name = map_name

    @property
    def target_id(self) -> str:
        return self.workflow.get("target_id", "")

    @property
    def steps(self) -> list:
        return self.workflow.get("steps", [])

    def to_dict(self) -> dict:
        """序列化为可 JSON 化的 dict。"""
        return {
            "workflow": self.workflow,
            "portal_fragment": self.portal_fragment,
            "trajectory_events": self.trajectory_events,
            "map_id": self.map_id,
            "map_name": self.map_name,
        }


def main():
    """命令行入口：python -m ingest.compiler.map_converter <map_dir> [output_dir]"""
    import sys
    if len(sys.argv) < 2:
        print("用法: python -m ingest.compiler.map_converter <map_dir> [output_dir]")
        sys.exit(2)
    map_dir = Path(sys.argv[1])
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    conv = 地图Converter()
    results = conv.convert_directory(map_dir, output_dir)
    print(f"转换完成: {len(results)} 个地图")
    for r in results:
        print(f"  {r.map_id}: {r.map_name} "
              f"({len(r.steps)} steps, "
              f"portal={'Y' if r.portal_fragment else 'N'}, "
              f"traj={len(r.trajectory_events)} events)")


if __name__ == "__main__":
    main()
