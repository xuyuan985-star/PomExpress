"""ObservationStore：世界实体 → 最近观测记录（ 解耦 + / 时空约束）。

记录含 bbox/置信度/时间戳/帧号——executor 消费时校验时效与置信度，
拒绝"看到 3 秒前的位置"或"0.2 置信度的猜测"（Belief state 基础）。
"""
import time


class ObservationRecord:
    __slots__ = ("bbox", "timestamp", "confidence", "frame_id")

    def __init__(self, bbox, timestamp=None, confidence=None, frame_id=None):
        self.bbox = bbox                        # (x,y) 或 [x1,y1,x2,y2]（归一化 0-1）
        self.timestamp = timestamp if timestamp is not None else time.time()
        self.confidence = confidence            # -1，None = 未知
        self.frame_id = frame_id                # 来源帧（可空）

    def is_stale(self, max_age=1.5):
        return time.time() - self.timestamp > max_age


class ObservationStore:
    def __init__(self, max_entries=64, protected_ids=None):
        self._data = {}
        self._max = max_entries
        # 受保护目标（任务关键）不参与容量淘汰
        self._protected = set(protected_ids or [])

    def set(self, entity_id, bbox, confidence=None, frame_id=None):
        # bbox 结构校验（2 中心点 / 4 角点；越界拒绝——clamp 到
        # 边缘会点击错误对象，必须显式报错）
        if not isinstance(bbox, (tuple, list)) or len(bbox) not in (2, 4):
            raise ValueError(f"非法 bbox 结构: {bbox!r}（需要 2 点或 4 点）")
        try:
            nums = [float(v) for v in bbox]
        except (TypeError, ValueError):
            raise ValueError(f"bbox 含非数值: {bbox!r}")
        if any(v < 0.0 or v > 1.0 for v in nums):
            raise ValueError(f"bbox 越界 [0,1]: {nums}")
        self._data[entity_id] = ObservationRecord(
            tuple(nums), confidence=confidence, frame_id=frame_id)
        if len(self._data) > self._max:
            for k in list(self._data):
                if k in self._protected:
                    continue
                del self._data[k]
                if len(self._data) <= self._max:
                    break

    def protect(self, entity_id):
        """标记关键目标——不参与容量淘汰。"""
        self._protected.add(entity_id)

    def get(self, entity_id):
        """返回不可变快照（拷贝），observer/executor 各持一份
        executor 读取期间 observer 更新不会看到半更新状态。"""
        rec = self._data.get(entity_id)
        if rec is None:
            return None
        return ObservationRecord(
            bbox=tuple(rec.bbox),
            timestamp=rec.timestamp,
            confidence=rec.confidence,
            frame_id=rec.frame_id,
        )

    def get_valid(self, entity_id, max_age=1.5, min_confidence=None):
        """统一有效观测入口——时效/置信度校验内置，
        业务层禁止绕过（get 仅诊断用）。"""
        obs = self.get(entity_id)
        if obs is None:
            return None
        if obs.is_stale(max_age):
            return None
        if min_confidence is not None and obs.confidence is not None \
                and obs.confidence < min_confidence:
            return None
        return obs

    def invalidate(self, entity_id):
        """观测失效（帧变化时旧证据不得继续驱动动作）。"""
        self._data.pop(entity_id, None)

    def snapshot(self, entity_id):
        return self.get(entity_id)

    def clear(self):
        self._data.clear()

    def snapshot_all(self):
        """全量快照（bbox 视图）——诊断/展示用。"""
        return {k: v.bbox for k, v in self._data.items()}
