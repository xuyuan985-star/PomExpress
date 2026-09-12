import json
import threading
from collections import deque
from pathlib import Path

from runtime.events.schema import WorldEvent


# 内存事件流设上限（防长期运行无限增长）；持久化流不受限
EVENT_RING_CAPACITY = 5000


class EventBus:
    def __init__(self, persist_path=None):
        self._lock = threading.Lock()
        self._subscribers = []
        self._events = deque(maxlen=EVENT_RING_CAPACITY)
        self._seq = 0
        self._persist_path = persist_path
        self._fh = None
        if persist_path:
            Path(persist_path).parent.mkdir(parents=True, exist_ok=True)
            # 隐藏 Bug 审查：持久化文件无轮转——24h 高频事件（action/observation）
            # 无限增长。超过 20MB 轮转（.1 保留一份）。
            self._rotate_if_large(persist_path)
            self._fh = open(persist_path, "a", encoding="utf-8")

    @staticmethod
    def _rotate_if_large(path, max_bytes=20 * 1024 * 1024):
        import os
        try:
            if os.path.getsize(path) > max_bytes:
                backup = f"{path}.1"
                if os.path.exists(backup):
                    os.remove(backup)
                os.rename(path, backup)
        except OSError:
            pass

    def subscribe(self, callback, weak=False):
        # 订阅者列表并发修改（GUI 线程注册 / runner 线程 publish 快照）
        # weak=True 时存弱引用——对象销毁自动失效（防内存泄漏）
        with self._lock:
            if weak:
                import weakref
                try:
                    ref = weakref.ref(callback)
                except TypeError:
                    ref = None
                if ref is not None:
                    self._subscribers.append(("weak", ref))
                    return
            self._subscribers.append(("strong", callback))

    def unsubscribe(self, callback):
        """取消订阅——GUI 窗口销毁时必调（防已删 Qt 信号被 publish 调用）。"""
        with self._lock:
            for kind, ref in self._subscribers:
                if kind == "strong" and ref is callback:
                    self._subscribers.remove((kind, ref))
                    return
            try:
                self._subscribers.remove(("strong", callback))
            except ValueError:
                pass

    def publish(self, event: WorldEvent):
        to_persist = None
        with self._lock:
            self._seq += 1
            event.sequence_id = self._seq    # 按发布序打全局序号
            self._events.append(event)
            if self._fh:
                # write 在锁内（顺序保证），flush 移出锁
                # 磁盘慢不阻塞其他发布者（批量 flush 语义）
                self._fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                to_persist = self._fh
        if to_persist is not None:
            try:
                to_persist.flush()
            except (OSError, ValueError):
                # 审查 P1：close 后另一线程 flush → ValueError——一并捕获
                import logging
                logging.getLogger("runtime.events").warning(
                    "事件持久化 flush 失败（可能已 close）", exc_info=True)
        callbacks = []
        with self._lock:
            for kind, ref in list(self._subscribers):
                if kind == "weak":
                    cb = ref()
                    if cb is None:
                        self._subscribers.remove((kind, ref))    # 已亡对象自动清理
                        continue
                    callbacks.append(cb)
                else:
                    callbacks.append(ref)
        for cb in callbacks:
            try:
                cb(event)
            except Exception as e:
                # 事件系统不能反向影响执行链——订阅者异常只记录
                import logging
                logging.getLogger("runtime.events").warning(
                    "subscriber error on %s: %s", event.type, e, exc_info=True)
        try:
            from runtime import db
            db.record_event(event)
        except Exception as e:
            import logging
            logging.getLogger("runtime.events").warning("db.record_event failed: %s", e)

    def replay(self, execution_id=None):
        with self._lock:
            events = list(self._events)
        if execution_id:
            events = [e for e in events if e.execution_id == execution_id]
        return events

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None

    @classmethod
    def load(cls, path):
        bus = cls()
        if Path(path).exists():
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:    # 半行/损坏日志跳过（断电场景）
                    data = json.loads(line)
                except Exception:
                    continue
                try:
                    bus._events.append(WorldEvent(**data))
                except Exception:
                    continue
        # 恢复序号——load 后 _seq=0 会重复 sequence_id（排序/去重破坏）
        bus._seq = max((e.sequence_id or 0 for e in bus._events), default=0)
        return bus


default_bus = EventBus()
