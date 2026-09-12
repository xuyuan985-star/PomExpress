"""Local 视觉桥接（数据内化）：截图 / OCR / 模板匹配 / 坐标全自研。

坐标体系：截图内坐标 + screenshot_pos（客户区左上角绝对屏幕坐标）= 绝对坐标；
>1920px 截图由 screenshot_scale_factor 归一化。执行器不感知 relative/DPI。
不依赖 外部参考实现 目录（win_capture / ocr_engine / template_backend）。
"""

import logging

log = logging.getLogger("runtime.drivers.local.vision")

# OCR 文本清洗（形近字归一：宝箱O→宝箱0，全角→半角）
_OCR_TRANSLATE = str.maketrans({
    "O": "0", "o": "0", "l": "1", "I": "1",
    "S": "5", "s": "5", "B": "8", "b": "8",
    "G": "6", "g": "6", "Z": "2", "z": "2",
})


def normalize_ocr(text):
    """OCR 文本清洗——全角→半角 + ASCII 形近字归一（防 宝箱O/宝箱0 匹配失败）。"""
    if not text:
        return text
    out = []
    for ch in text:
        code = ord(ch)
        if 0xFF10 <= code <= 0xFF19:    # 全角数字 ０-９
            out.append(chr(code - 0xFEE0))
            continue
        if 0xFF21 <= code <= 0xFF3A or 0xFF41 <= code <= 0xFF5A:    # 全角字母
            out.append(chr(code - 0xFEE0))
            continue
        out.append(ch)
    s = "".join(out)
    # 审查 P1：isascii 守卫使中文串内字母不映射、纯英文词被误伤（UID→U1D
    # 会破坏 gate 关键词匹配）。正确语义：按"字母段"处理——段内任一字
    # 母邻接数字（OCR 数字串误读场景）则整段映射（1OO→100），
    # 孤立英文词（UID/CREDIT）不映射。
    mapped = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if "A" <= ch <= "Z" or "a" <= ch <= "z":
            # 收集连续字母段
            j = i
            while j < n and (s[j].isalpha()):
                j += 1
            segment = s[i:j]
            # 段邻接数字？（段前或段后是数字）
            near_digit = (i > 0 and s[i - 1].isdigit()) or \
                         (j < n and s[j].isdigit())
            if near_digit:
                mapped.append("".join(
                    _OCR_TRANSLATE.get(ord(c), c) for c in segment))
            else:
                mapped.append(segment)
            i = j
        else:
            mapped.append(ch)
            i += 1
    return "".join(mapped)


from abc import ABC, abstractmethod


class VisionInterface(ABC):
    """视觉接口抽象——Fake 与真实实现同契约（防测试 PASS 实机缺方法）。

    线程安全：构造线程安全（锁内初始化）；实例方法预期单线程调用（thread_safe=False）。
    """

    thread_safe = False

    @abstractmethod
    def screenshot_path(self, out_dir):
        """截图落盘 → 路径（观测帧用）。"""

    @abstractmethod
    def take_screenshot(self, crop=None):
        """截图 → (PIL.Image, screenshot_pos, scale_factor)。"""

    @abstractmethod
    def ocr_lines(self, crop=(0, 0, 1, 1)):
        """OCR → [(text, box), ...]。"""

    @abstractmethod
    def find_template(self, path, threshold=0.8, max_retries=1):
        """模板匹配 → 绝对坐标框或 None。"""

    @abstractmethod
    def to_absolute(self, norm_x, norm_y):
        """归一化(0-1) → 绝对屏幕坐标。"""


def normalize_image(img, target="RGB"):
    """图像格式统一（RGB/BGR/RGBA → 目标通道，入口一致化）。

    返回新对象；已是目标模式则原样返回。
    """
    if img is None:
        return None
    if getattr(img, "mode", None) == target:
        return img
    if hasattr(img, "convert"):
        return img.convert(target)
    return img


def merge_ocr_lines(results, y_tol=16.0):
    """行级合并：y 中心相近的碎片按 x 排序水平拼接（m7 块合并适配版）。

    输入 [(text, box)] → 输出 [(merged_text, merged_box)]，box 为碎片
    并集（min/max 四点）。合并规则：box 中心 y 差 < y_tol 视为同行。
    直接拼接不补空格——关键词包含匹配要求连续无间隙（汉字间距本就大）。
    模块级函数：driver.ocr_lines 与观察者 OCRAdapter 共用。
    """
    if not results:
        return []
    parsed = []
    for text, box in results:
        if not box:
            parsed.append({"text": text, "x": 0, "x_max": 0, "y": 0,
                           "min_x": 0, "max_x": 0, "min_y": 0, "max_y": 0})
            continue
        xs = [c[0] for c in box]
        ys = [c[1] for c in box]
        parsed.append({"text": text,
                       "x": min(xs), "x_max": max(xs),
                       "y": (min(ys) + max(ys)) / 2,
                       "min_x": min(xs), "max_x": max(xs),
                       "min_y": min(ys), "max_y": max(ys)})
    parsed.sort(key=lambda p: (p["y"], p["x"]))
    lines = []    # 每行: {y, items}
    for p in parsed:
        placed = False
        for line in lines:
            if abs(line["y"] - p["y"]) <= y_tol:
                line["items"].append(p)
                line["y"] = (line["y"] + p["y"]) / 2
                placed = True
                break
        if not placed:
            lines.append({"y": p["y"], "items": [p]})
    out = []
    for line in lines:
        items = sorted(line["items"], key=lambda it: it["x"])
        text = "".join(it["text"] for it in items)
        box = [(min(it["min_x"] for it in items), min(it["min_y"] for it in items)),
               (max(it["max_x"] for it in items), min(it["min_y"] for it in items)),
               (max(it["max_x"] for it in items), max(it["max_y"] for it in items)),
               (min(it["min_x"] for it in items), max(it["max_y"] for it in items))]
        out.append((text, box))
    return out


class ScreenVision(VisionInterface):
    """视觉桥接（数据内化：全自研栈——不再 import m7 的 module.*）。

    截图：runtime/win_capture（PrintWindow 后台 → 前台 mss 兜底）
    OCR：runtime/ocr_engine（rapidocr 直连）
    模板匹配：runtime/input/template_backend（cv2 多尺度）
    窗口：runtime/win_capture（自研枚举）
    """

    name = "local"

    def __init__(self):
        # 数据内化：构造零外部依赖（不再 ensure_local_env / import module）
        self._ocr_engine = None
        self._matcher = None
        # -G：最近一次截图的结构质量（成功 ≠ 正确，供调用方在识别前决策）
        self.last_quality = None
        self._validator = None

    @property
    def validator(self):
        if self._validator is None:
            from runtime.vision_quality import FrameValidator
            self._validator = FrameValidator()
        return self._validator

    @property
    def matcher(self):
        """自研 cv2 多尺度模板匹配器（懒加载）。"""
        if self._matcher is None:
            from runtime.input.template_backend import TemplateMatcher
            self._matcher = TemplateMatcher()
        return self._matcher

    @property
    def ocr(self):
        """OCR 引擎（懒加载——rapidocr 模型首次调用加载）；不可用返回 None。"""
        if self._ocr_engine is None:
            try:
                from runtime.ocr_engine import _get_engine
                self._ocr_engine = _get_engine()
            except Exception:
                self._ocr_engine = None
        return self._ocr_engine

    def _quality_ok(self):
        """BUG-VIS-001：检查最近一次截图质量——坏帧 fail-closed 拒绝使用。

        FrameValidator.validate 阈值依据（vision_quality.py）：
        - black: dark pixels > 99%
        - white: light pixels > 99%
        - heavy_black_borders: edge dark ratio > 80%
        - size_mismatch: 期望分辨率不符
        - empty frame: arr.size == 0
        正常游戏画面不会触发（实测 black_ratio < 0.05）。
        """
        q = self.last_quality
        if q is None:
            return True    # 尚未截图——无质量数据，放行
        if q.quality != "ok":
            log.warning(
                "坏帧拒绝使用 (quality=%s, source=%s, reason=%s)",
                q.quality, q.source, q.reason)
            return False
        return True

    def _capture(self, crop=None):
        """自研截图：PrintWindow 后台优先 → 前台 mss 兜底。

        返回 (PIL.Image, client_pos, scale)；client_pos = (left, top, right, bottom)
        客户区绝对屏幕坐标。crop（截图内归一化 0-1 四元组）在 PrintWindow
        路径支持；前台兜底路径忽略 crop（整帧）。
        """
        from runtime.win_capture import (find_game_window, try_capture_window,
                                         capture_game_foreground)
        import win32gui
        game = find_game_window()
        if game is None:
            raise RuntimeError("no game window")
        try:
            img = try_capture_window(game)
            source = "print_window"
        except Exception:
            img = capture_game_foreground(game)
            source = "foreground_mss"
        left, top = win32gui.ClientToScreen(game["hwnd"], (0, 0))
        w, h = game["client"]
        if crop is not None and source == "print_window":
            x1, y1, x2, y2 = crop
            iw, ih = img.size
            img = img.crop((int(x1 * iw), int(y1 * ih),
                            int(x2 * iw), int(y2 * ih)))
        return img, (left, top, left + w, top + h), 1.0

    def take_screenshot(self, crop=None):
        """截图 → (PIL.Image, screenshot_pos, scale_factor)。

        -G：返回前做结构质量校验（全黑/全白/黑边），记入 last_quality
        不抛异常（截图本身成功），由调用方在进识别前决策。
        """
        out = self._capture(crop=crop)
        self.last_quality = self.validator.validate(out[0], source="self")
        return out

    def screenshot_path(self, out_dir):
        """截图落盘，返回路径（观测帧用）。

        BUG-VIS-001：坏帧拒绝落盘——黑帧/空帧会让识别输出无意义结果。
        """
        import time
        from pathlib import Path
        shot = self.take_screenshot()
        img, _, _ = shot
        if not self._quality_ok():
            raise RuntimeError("screenshot rejected: bad frame quality")
        p = Path(out_dir) / f"shot_{int(time.time() * 1000)}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        img.save(p, "JPEG", quality=90)
        return p

    def ocr_lines(self, crop=(0, 0, 1, 1)):
        """OCR 返回 [(text, box)]，box 为四点 [(x,y),...] 截图内坐标。

        BUG-VIS-001：坏帧拒绝 OCR——空帧/黑帧 OCR 返回空列表，
        调用方可能误判为"没有文本"而走 fail-open。
        """
        img, _, _ = self.take_screenshot(crop=crop)
        if not self._quality_ok():
            raise RuntimeError("ocr rejected: bad frame quality")
        from runtime.ocr_engine import ocr_image
        raw = ocr_image(img)
        return merge_ocr_lines([(normalize_ocr(t), b) for t, b in raw])

    def find_text(self, text, include=True, max_retries=1, crop=None):
        """OCR 定位文本 → 绝对坐标框 ((left,top),(right,bottom)) 或 None。

        include=True：文本包含目标词即命中；False：不包含。
        BUG-VIS-001：坏帧直接返回 None（不重试——黑帧不会变好帧）。
        """
        import time
        from runtime.ocr_engine import ocr_image
        for _ in range(max(1, max_retries or 1)):
            try:
                # 单次截图：pos 与 OCR 同一帧（审查：原实现 take_screenshot 后
                # 再调 ocr_lines 会二次截图——两帧间窗口移动则坐标错位）
                img, pos, _ = self.take_screenshot(crop=crop)
                if not self._quality_ok():
                    break    # 坏帧不重试
                left0, top0, _, _ = pos
                raw = ocr_image(img)
                for t, box in merge_ocr_lines(
                        [(normalize_ocr(x), b) for x, b in raw]):
                    hit = (text in t) if include else (text not in t)
                    if hit and box:
                        xs = [p[0] for p in box]
                        ys = [p[1] for p in box]
                        return ((left0 + min(xs), top0 + min(ys)),
                                (left0 + max(xs), top0 + max(ys)))
            except Exception:
                pass
            time.sleep(0.5)
        return None

    def find_template(self, path, threshold=0.8, max_retries=1):
        """模板匹配 → 绝对坐标框或 None（自研 TemplateMatcher.locate）。

        返回 (val, cx, cy) 中心坐标（与原 m7 find_element 语义兼容——调用方
        只判 None / 取坐标）。
        BUG-VIS-001：预检帧质量——matcher 内部用 mss 独立截图，
        若显示层已返回坏帧则 matcher 也必然拿到坏帧，直接返回 None。
        """
        if self.matcher is None:
            return None
        # 质量预检：take_screenshot 触发 validator，检查质量
        self.take_screenshot()
        if not self._quality_ok():
            return None
        return self.matcher.locate(path)

    def to_absolute(self, norm_x, norm_y):
        """归一化坐标(0-1) → 绝对屏幕坐标（观测位置消费，执行细节）。

        归一化边界 clamp； round 而非截断。
        BUG-VIS-001：坏帧拒绝换算——空帧尺寸可能异常导致坐标算错。
        """
        img, screenshot_pos, scale = self.take_screenshot()
        if not self._quality_ok():
            raise RuntimeError("to_absolute rejected: bad frame quality")
        left, top, w, h = screenshot_pos
        img_w, img_h = img.size
        norm_x = max(0.0, min(1.0, float(norm_x)))
        norm_y = max(0.0, min(1.0, float(norm_y)))
        return (left + round(norm_x * img_w / (scale or 1)),
                top + round(norm_y * img_h / (scale or 1)))
