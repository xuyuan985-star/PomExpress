"""白名单/黑名单 + get_map_list 断点续跑。

从 外部参考实现 的 Map 类（map.py）提取白名单/黑名单逻辑 + 地图列表
排序/切片，适配到本项目的知识包/地图目录结构。

核心功能：
  1. 白名单模式（allowlist）：只运行白名单中的地图
  2. 黑名单模式（forbid_map）：跳过黑名单中的地图
  3. get_map_list(start, start_in_mid)：从起始地图切片列表
  4. 断点续跑（checkpoint）：持久化最后完成的地图编号，
     中断后从断点恢复

断点持久化：
  - 写入 .map_checkpoint.json（map_dir 下）
  - 记录 last_completed_map_id 和 timestamp
  - 恢复时读 checkpoint → 从 last_completed + 1 开始
  - 手动指定 start 优先于 checkpoint

地图排序：
  与 地图 MapInfo.sort_json_files 对齐
  按文件名中 X-Y_N 的数字段排序（如 1-1_0 < 1-1_1 < 1-2_0）
"""
import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("runtime.map_runner")

# 断点文件名
CHECKPOINT_FILE = ".map_checkpoint.json"


class MapListError(Exception):
    """地图列表错误。"""


def sort_map_files(filenames: list[str]) -> list[str]:
    """对地图文件名排序（与 地图 MapInfo.sort_json_files 对齐）。

    排序键：从文件名提取数字段做整数排序。
    map_1-1_0.json → [1, 1, 0]
    map_2-5_8.json → [2, 5, 8]
    """
    def sort_key(filename: str) -> list[int]:
        # 去掉 map_ 前缀和 .json 后缀
        stem = filename.replace("map_", "").replace(".json", "")
        # 替换分隔符为 _，然后分割
        parts = stem.replace("-", "_").split("_")
        result = []
        for p in parts:
            try:
                result.append(int(p))
            except ValueError:
                result.append(0)
        return result

    return sorted(filenames, key=sort_key)


def get_map_list(map_files: list[str], start: str,
                 start_in_mid: bool = False) -> list[str]:
    """获取从 start 开始的有序地图列表（与 地图 Map.get_map_list 对齐）。

    Args
        map_files: 排序后的文件名列表（如 ["map_1-1_0.json", ...]）
        start: 起始地图编号（如 "1-1_0"）
        start_in_mid: True = 优先地图模式（start 后续 + 从头补齐）
                      False = 从 start 到末尾

    Returns
        有序文件名列表
    """
    start_file = f"map_{start}.json"
    if start_file not in map_files:
        raise MapListError(
            f"起始地图 {start_file} 不在地图列表中")

    start_index = map_files.index(start_file)

    if start_in_mid:
        # 优先地图模式：start 后续 + 从头到 start 之前
        tail = map_files[start_index:]
        head = map_files[:start_index]
        return tail + head
    else:
        # 正常模式：从 start 到末尾
        return map_files[start_index:]


def check_allowlist(map_name: str, allowlist: list[str] | None,
                    allowlist_mode: bool = False) -> bool:
    """白名单检查：地图是否应该跳过（不在白名单中 → True = 跳过）。

    与 地图 Map.check_allowlist_maps 对齐：
    - 白名单匹配是前缀匹配（map_name 按 '-' 分割取第一段）
    - allowlist_mode 为 False 时不过滤（全部通过）

    Args
        map_name: 地图名称（如 "基座舱段-1"）
        allowlist: 白名单列表（如 ["基座舱段", "收容舱段"]）
        allowlist_mode: 是否启用白名单模式

    Returns
        True = 跳过此地图（不在白名单中）
        False = 放行此地图
    """
    if not allowlist_mode:
        return False

    if not allowlist:
        # 白名单为空 → 全部跳过（与 地图 行为一致）
        log.info("白名单模式但白名单为空，跳过地图 %s", map_name)
        return True

    # 前缀匹配：取地图名第一段（'-' 之前）
    first_part = map_name.split("-")[0]
    if first_part in allowlist:
        return False    # 在白名单中 → 放行

    log.info("地图 %s 不在白名单中，跳过", map_name)
    return True


def check_forbidden(map_name: str, forbidden: list[str] | None) -> bool:
    """黑名单检查：地图是否应该跳过（在黑名单中 → True = 跳过）。

    与 地图 Map.check_forbidden_maps 对齐：
    - 黑名单匹配是前缀匹配（map_name 按 '-' 分割取第一段）

    Args
        map_name: 地图名称（如 "基座舱段-1"）
        forbidden: 黑名单列表（如 ["禁闭舱段"]）

    Returns
        True = 跳过此地图（在黑名单中）
        False = 放行此地图
    """
    if not forbidden:
        return False

    if not all(isinstance(item, str) for item in forbidden):
        log.warning("黑名单格式错误：应只包含字符串")
        return False

    first_part = map_name.split("-")[0]
    if first_part in forbidden:
        log.info("地图 %s 在黑名单中，跳过", map_name)
        return True

    return False


def should_skip_map(map_name: str, allowlist: list[str] | None = None,
                    allowlist_mode: bool = False,
                    forbidden: list[str] | None = None) -> bool:
    """综合检查：白名单 + 黑名单。

    Returns
        True = 跳过此地图
        False = 放行此地图
    """
    # 白名单优先（如果启用白名单但不在白名单中 → 跳过）
    if check_allowlist(map_name, allowlist, allowlist_mode):
        return True
    # 黑名单检查
    if check_forbidden(map_name, forbidden):
        return True
    return False


# 断点续跑

class CheckpointManager:
    """断点持久化管理：记录最后完成的地图编号，支持中断恢复。

    持久化格式（.map_checkpoint.json）：
    {
        "last_completed_map_id": "1-2_1"
        "last_completed_map_name": "收容舱段-1"
        "timestamp": 1234567890
        "total_completed": 5
        "total_maps": 30
    }
    """

    def __init__(self, map_dir: Path | str):
        self.map_dir = Path(map_dir)
        self.checkpoint_path = self.map_dir / CHECKPOINT_FILE
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self):
        """加载断点文件。"""
        if self.checkpoint_path.exists():
            try:
                self._data = json.loads(
                    self.checkpoint_path.read_text(encoding="utf-8"))
                log.info("加载断点: 最后完成 %s",
                         self._data.get("last_completed_map_id"))
            except Exception as e:
                log.warning("断点文件损坏，重置: %s", e)
                self._data = {}
        else:
            self._data = {}

    def _save(self):
        """保存断点文件。"""
        try:
            self.checkpoint_path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as e:
            log.error("保存断点失败: %s", e)

    @property
    def last_completed(self) -> str | None:
        """最后完成的地图编号（如 "1-2_1"）。"""
        return self._data.get("last_completed_map_id")

    @property
    def last_completed_name(self) -> str | None:
        """最后完成的地图名称。"""
        return self._data.get("last_completed_map_name")

    @property
    def total_completed(self) -> int:
        """已完成的地图数量。"""
        return self._data.get("total_completed", 0)

    @property
    def total_maps(self) -> int:
        """总地图数量。"""
        return self._data.get("total_maps", 0)

    def record_completion(self, map_id: str, map_name: str = "",
                          total_maps: int = 0):
        """记录完成一个地图。

        Args
            map_id: 地图编号（如 "1-2_1"）
            map_name: 地图名称（如 "收容舱段-1"）
            total_maps: 总地图数量（首次记录时传入）
        """
        self._data["last_completed_map_id"] = map_id
        self._data["last_completed_map_name"] = map_name
        self._data["timestamp"] = int(time.time())
        self._data["total_completed"] = self._data.get(
            "total_completed", 0) + 1
        if total_maps > 0:
            self._data["total_maps"] = total_maps
        self._save()
        log.info("记录完成: %s (%s), 进度 %d/%d",
                 map_id, map_name,
                 self._data["total_completed"],
                 self._data.get("total_maps", 0))

    def get_resume_start(self, all_maps: list[str],
                        manual_start: str | None = None) -> str | None:
        """获取恢复起始地图编号。

        优先级：
        1. 手动指定 start（最高优先）
        2. 断点记录的 last_completed 的下一个
        3. None（从头开始）

        Args
            all_maps: 排序后的地图编号列表（如 ["1-1_0", "1-1_1", ...]）
            manual_start: 手动指定的起始地图编号

        Returns
            起始地图编号，或 None（无断点且无手动指定）
        """
        # . 手动指定优先
        if manual_start:
            log.info("使用手动指定起始: %s", manual_start)
            return manual_start

        # . 断点恢复
        last = self.last_completed
        if not last:
            return None

        # 找到 last 在列表中的位置 → 返回下一个
        try:
            idx = all_maps.index(last)
        except ValueError:
            log.warning("断点地图 %s 不在列表中，从头开始", last)
            return all_maps[0] if all_maps else None

        if idx + 1 < len(all_maps):
            next_map = all_maps[idx + 1]
            log.info("断点恢复: 从 %s 的下一个 %s 开始", last, next_map)
            return next_map
        else:
            log.info("断点 %s 已是最后一个地图，无更多地图", last)
            return None

    def clear(self):
        """清除断点（从头开始）。"""
        self._data = {}
        try:
            self.checkpoint_path.unlink()
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("清除断点文件失败: %s", e)
        log.info("断点已清除")

    def is_complete(self, all_maps: list[str] | None = None) -> bool:
        """检查是否全部完成。

        Args
            all_maps: 全部地图编号列表（用于检查 last 是否是最后一个）
        """
        if all_maps and self.last_completed:
            try:
                return all_maps.index(self.last_completed) == len(all_maps) - 1
            except ValueError:
                return False
        return False


class MapRunner:
    """地图运行器：整合白名单/黑名单 + 地图列表 + 断点续跑。

    用法：
        runner = MapRunner(map_dir, allowlist=["基座舱段"]
                          allowlist_mode=True)
        map_list = runner.get_filtered_map_list(start="1-1_0")
        for map_id in map_list
            # 处理每个地图
            runner.checkpoint.record_completion(map_id, map_name)
    """

    def __init__(self, map_dir: Path | str,
                 allowlist: list[str] | None = None,
                 allowlist_mode: bool = False,
                 forbidden: list[str] | None = None):
        self.map_dir = Path(map_dir)
        self.allowlist = allowlist
        self.allowlist_mode = allowlist_mode
        self.forbidden = forbidden
        self.checkpoint = CheckpointManager(self.map_dir)
        self._map_files: list[str] | None = None
        self._map_names: dict[str, str] = {}

    @property
    def map_files(self) -> list[str]:
        """排序后的地图文件名列表。"""
        if self._map_files is None:
            files = [
                f.name for f in self.map_dir.glob("map_*.json")
                if f.is_file()
            ]
            self._map_files = sort_map_files(files)
        return self._map_files

    @property
    def map_ids(self) -> list[str]:
        """排序后的地图编号列表（去 map_ 前缀和 .json 后缀）。"""
        return [
            f.replace("map_", "").replace(".json", "")
            for f in self.map_files
        ]

    def get_map_name(self, map_id: str) -> str:
        """读取地图名称（从 JSON 的 name 字段）。"""
        if map_id in self._map_names:
            return self._map_names[map_id]
        path = self.map_dir / f"map_{map_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            name = data.get("name", map_id)
        except Exception:
            name = map_id
        self._map_names[map_id] = name
        return name

    def get_filtered_map_list(self, start: str | None = None,
                              start_in_mid: bool = False,
                              resume_from_checkpoint: bool = True,
                              ) -> list[tuple[str, str]]:
        """获取过滤后的地图列表（白名单/黑名单 + 断点续跑）。

        Args
            start: 手动指定起始地图编号（优先于断点）
            start_in_mid: 优先地图模式（start 后续 + 从头补齐）
            resume_from_checkpoint: 是否从断点恢复

        Returns
            [(map_id, map_name), ...] 过滤后的有序地图列表
        """
        all_ids = self.map_ids

        # 确定起始地图
        resume_start = None
        if resume_from_checkpoint and not start:
            resume_start = self.checkpoint.get_resume_start(all_ids)

        actual_start = start or resume_start or (
            all_ids[0] if all_ids else None)

        if not actual_start:
            return []

        # 切片地图列表
        try:
            files = get_map_list(self.map_files, actual_start, start_in_mid)
        except MapListError as e:
            log.error("地图列表切片失败: %s", e)
            return []

        # 过滤白名单/黑名单
        result = []
        for f in files:
            map_id = f.replace("map_", "").replace(".json", "")
            map_name = self.get_map_name(map_id)
            if should_skip_map(map_name, self.allowlist,
                               self.allowlist_mode, self.forbidden):
                continue
            result.append((map_id, map_name))

        log.info("过滤后地图列表: %d 张（总计 %d，过滤 %d）",
                 len(result), len(files), len(files) - len(result))
        return result

    def record_completion(self, map_id: str, map_name: str = ""):
        """记录完成一个地图（代理到 checkpoint）。"""
        if not map_name:
            map_name = self.get_map_name(map_id)
        self.checkpoint.record_completion(
            map_id, map_name, total_maps=len(self.map_ids))

    def is_all_complete(self) -> bool:
        """检查是否全部完成。"""
        return self.checkpoint.is_complete(self.map_ids)

    def reset(self):
        """重置断点（从头开始）。"""
        self.checkpoint.clear()
