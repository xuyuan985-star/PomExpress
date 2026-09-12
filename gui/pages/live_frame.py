"""共享：实时帧落盘 + 背景图显示（D7，S2-B）。

根因（两页面共用）：本环境 QLabel.setPixmap 实测失效（pixmap() 返回 null）——
统一走样式 background-image（pixmap 存临时文件）。

原重复实现：
command_deck.py ObservationSnapshot.set_frame（lines 100-117）
observation.py (was placeholder.py:537-549) _show_pix
"""
from pathlib import Path
from config.settings import data_path

# 共享文件名（同屏下两个页面共用——分页用不同文件名会撞缓存/误导）。
# 现状：单页轮询 3s 写盘一次；如需分页可加 caller namespace（暂不必要）。
LIVE_FRAME_FILE = "live_frame.png"


def live_frame_path() -> Path:
    """实时帧临时文件路径（logs/live_frame.png）。"""
    # 统一从项目根目录定位（行为保持——与原 parent.parent.parent 等价）
    return data_path("logs") / LIVE_FRAME_FILE


def save_live_frame(pixmap) -> Path:
    """保存 QPixmap → logs/live_frame.png → 返回路径。

    调用方拿到路径后用 `background-image: url(<path>)` 设到 QLabel 样式表。
    """
    p = live_frame_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(str(p))
    return p
