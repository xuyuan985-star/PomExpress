"""自研 OCR 引擎：rapidocr 直连（数据内化——不再经 m7 的 module.ocr）。

依赖：rapidocr（m7_venv 内，本项目自己的环境）。模型首次调用才
加载（约 1-2s），线程安全懒加载单例。
"""
import threading

import numpy as np

_ENGINE = None
_LOCK = threading.Lock()


def _get_engine():
    """rapidocr 引擎单例（懒加载）。失败抛异常（调用方决策降级）。"""
    global _ENGINE
    if _ENGINE is None:
        with _LOCK:
            if _ENGINE is None:
                from rapidocr import RapidOCR
                _ENGINE = RapidOCR()
    return _ENGINE


def ocr_image(image):
    """图像（PIL/np 数组）→ [(text, box)]。

    box 为四点 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] 截图内坐标
    与 runtime/vision 的 merge_ocr_lines 兼容。无可识别文本返回空列表。
    """
    if image is None:
        return []
    arr = np.asarray(image)
    res = _get_engine()(arr)
    # rapidocr 3.8：RapidOCROutput（txts/boxes/scores 属性）——兼容 tuple 旧 API
    if isinstance(res, tuple):
        result, _ = res
        out = []
        for item in result or []:
            try:
                box, text = item[0], item[1]
            except Exception:
                continue
            if text:
                out.append((str(text), box))
        return out
    txts = list(getattr(res, "txts", None) or [])
    boxes = getattr(res, "boxes", None)
    if boxes is None:
        boxes = []
    out = []
    for i, t in enumerate(txts):
        if not t:
            continue
        box = None
        if i < len(boxes):
            box = [[float(p[0]), float(p[1])] for p in boxes[i]]
        out.append((str(t), box))
    return out
