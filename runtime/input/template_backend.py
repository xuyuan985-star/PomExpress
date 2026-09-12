"""自研模板点击：cv2 多尺度匹配 + Win32 点击（绕过 Local 模板匹配）。

动机：Local 的 click_element 匹配受其环境依赖影响（本机被 security stub
污染导入链），且其 scale_and_match_template 分数低于自研 cv2 多尺度。
验证：真机截图 0.724 @ scale 0.55（模板 260px → 截图 1920）命中。
"""
import logging
import time

import cv2
import numpy as np

from runtime.input.win32_backend import Win32Backend


class TemplateMatcher:
    """截图 + 多尺度模板匹配（全屏坐标体系，1:1 映射屏幕坐标）。"""

    # 隐藏 Bug 审查：43 级 → 21 级（步长 0.1）——1440p 降采样后约 1s，
    # 级对 0.05 步长收益极低（相邻尺度匹配结果几乎相同）
    SCALES = np.round(np.arange(0.4, 2.6, 0.1), 2)

    def __init__(self, threshold=0.60, backend=None):
        self.threshold = threshold
        self.backend = backend or Win32Backend()
        self.last_match_ms = None    # 最近一次匹配耗时（ms）
        # 借鉴 Local ImageUtils/img_cache：模板按路径缓存（含 mtime 校验）
        # 避免每次 locate 重复读盘+全文件 sha256；文件变更自动重读重校验
        self._template_cache = {}

    def _screenshot(self):
        """截取游戏窗口所在显示器。

        mss 的 monitors[0] 是所有显示器的并集，monitors[1] 仅为主显示器——
        多显示器环境下游戏放在副屏时会截到主屏内容，匹配必然失败。此处按
        游戏窗口位置选择显示器；无法判定时回退主显示器并给出可见告警。
        """
        import mss
        with mss.mss() as sct:
            shot = sct.grab(self._pick_monitor(sct))
        # mss 新版本 shot.rgb 为 RGB bytes（无 alpha）
        return np.frombuffer(shot.rgb, dtype=np.uint8).reshape(
            shot.height, shot.width, 3)[:, :, ::-1]

    @staticmethod
    def _pick_monitor(sct):
        """按游戏窗口中心点选择所在显示器；不可判定时回退主显示器。"""
        monitors = sct.monitors
        if len(monitors) <= 2:
            return monitors[1]
        log = logging.getLogger("runtime.template_backend")
        try:
            import win32gui

            from runtime.win_capture import find_game_window
            game = find_game_window()
            if game is not None:
                left, top = win32gui.ClientToScreen(game["hwnd"], (0, 0))
                w, h = game["client"]
                cx, cy = left + w // 2, top + h // 2
                for mon in monitors[1:]:
                    if (mon["left"] <= cx < mon["left"] + mon["width"]
                            and mon["top"] <= cy < mon["top"] + mon["height"]):
                        if mon is not monitors[1]:
                            log.info("多显示器：游戏窗口位于非主显示器，"
                                     "截图区域已按窗口位置选择")
                        return mon
                log.warning("多显示器：游戏窗口位置未落在任何已知显示器范围内，"
                            "回退主显示器截图（匹配可能失败）")
        except Exception as e:
            log.warning("多显示器：无法定位游戏窗口所在显示器（%s: %s），"
                        "回退主显示器", type(e).__name__, e)
        return monitors[1]

    def _read_template(self, template_path):
        """借鉴 Local ImageUtils.read_image/read_template_with_mask：
        np.fromfile+imdecode（cv2.imread 不支持非 ASCII 路径——中文目录
        实测返回 None 且仅告警）；IMREAD_UNCHANGED 保留 alpha 通道，
        模板带透明区域时生成 mask（m7 机制——透明区不参与匹配，防背景干扰）。
        返回 (RGB 数组, mask 或 None)；文件不可读抛 ValueError。
        """
        import os
        try:
            mtime = os.path.getmtime(template_path)
        except OSError as e:
            raise ValueError(f"模板不可访问: {template_path} ({e})")
        cached = self._template_cache.get(template_path)
        if cached is not None and cached[0] == mtime:
            return cached[1], cached[2]
        data = np.fromfile(template_path, dtype=np.uint8)
        if data.size == 0:
            raise ValueError(f"模板读取失败(空文件): {template_path}")
        t = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if t is None:
            raise ValueError(f"模板解码失败(损坏?): {template_path}")
        mask = None
        if t.ndim == 3 and t.shape[2] == 4:
            alpha = t[:, :, 3]
            if np.any(alpha < 255):    # 存在非全透明像素 → mask 才有效
                mask = np.where(alpha > 0, 255, 0).astype(np.uint8)
            t = t[:, :, :3]    # 匹配只用 RGB
        elif t.ndim == 2:
            t = cv2.cvtColor(t, cv2.COLOR_GRAY2BGR)
        self._template_cache[template_path] = (mtime, t, mask)
        return t, mask

    def _load_template(self, template_path):
        """模板加载校验（空图/损坏 → 明确错误，不崩 matcher）。

        语义完整性——若知识包带 templates_manifest.json（sha256 清单），
        校验模板未被替换/篡改（文件被换内容但保留文件名 → false positive 源）。
        缓存命中（mtime 未变）跳过重读重校验。
        """
        t, mask = self._read_template(template_path)
        h, w = t.shape[:2]
        if h < 10 or w < 10:
            raise ValueError(f"模板尺寸过小 {w}x{h}: {template_path}")
        self._verify_manifest_hash(template_path)
        return t, mask

    def _verify_manifest_hash(self, template_path):
        """对照 templates_manifest.json 校验 sha256（manifest 存在时）。"""
        import hashlib
        from pathlib import Path
        try:
            tpl = Path(template_path)
            manifest = tpl.parent / "templates_manifest.json"
            if not manifest.exists():
                return    # 无清单 = 未启用完整性校验（兼容旧知识包）
            import json
            hashes = json.loads(manifest.read_text(encoding="utf-8")).get("hashes", {})
            expected = hashes.get(tpl.name)
            if expected is None:
                return    # 该模板未登记（新模板，未重新生成清单）
            actual = hashlib.sha256(tpl.read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(
                    f"模板哈希不匹配（文件被替换?）: {tpl.name}")
        except ValueError:
            raise
        except Exception:
            pass    # 清单损坏/不可读——不阻断匹配（降级为无校验）

    def locate(self, template_path, scale_range=None):
        """返回 (best_val, cx, cy) 屏幕绝对中心坐标；未命中返回 None。

        多尺度结果即隐式 NMS——只取全局最高分单点（同一目标不重复框）。
        审查 P0：中心偏移用【命中尺度的缩放尺寸】算。
        隐藏 Bug 审查：1440p 全屏 × 43 级多尺度实测 15.7s——每点击前卡 16 秒。
        降采样到 1280 宽工作空间匹配（坐标换算回全屏）——耗时 <1.5s。
        scale_range（workflow 知识声明，如 [0.9, 1.1]）：限制扫描尺度空间
        模板源分辨率已知时只扫声明区间（更快 + 防大尺度误匹配）；
        None 时扫全 SCALES（兼容无声明模板）。
        """
        import time
        t0 = time.time()
        t, mask = self._load_template(template_path)
        screen = self._screenshot()
        sh, sw = screen.shape[:2]
        # 降采样：工作空间固定 1280 宽（模板源自 1280 帧——1:1 尺度空间）
        work_scale = 1280.0 / sw if sw > 1280 else 1.0
        if work_scale < 1.0:
            work = cv2.resize(screen, (1280, int(sh * work_scale)),
                              interpolation=cv2.INTER_AREA)
        else:
            work = screen
        wh, ww = work.shape[:2]
        # scale_range（workflow 知识声明）：优先扫声明区间（外扩 40%
        # 实测数据声明与命中尺度有偏差，如 chest_A 声明 [0.9,1.1] 实际命中 0.55），
        # 区间内未命中再回退全 SCALES（保证 M1-A 已验证匹配不破坏）。
        scales = self.SCALES
        fallback_scales = None
        if scale_range:
            try:
                lo, hi = float(scale_range[0]) * 0.6, float(scale_range[1]) * 1.4
                s = self.SCALES[(self.SCALES >= lo) & (self.SCALES <= hi)]
                if s.size:
                    scales = s
                    fallback_scales = self.SCALES
                else:
                    scales = np.round(np.array([(lo + hi) / 2]), 2)
                    fallback_scales = self.SCALES
            except (TypeError, ValueError):
                pass    # 声明损坏 → 全量（与无声明一致）
        best = (0.0, 0, 0, 0.0)    # (val, x, y, scale)
        try:
            for scale in scales:
                th = cv2.resize(t, None, fx=scale, fy=scale,
                                interpolation=cv2.INTER_AREA)
                if th.shape[0] >= wh or th.shape[1] >= ww:
                    continue
                if mask is not None:
                    # 借鉴 Local：透明模板用 mask 匹配——透明区不参与，
                    # 防模板背景/异形区域干扰匹配。m7 用 TM_SQDIFF（分数语义
                    # 反转且作者自己备注混乱）——改 TM_CCORR_NORMED+mask：
                    # 分数 0-1 越高越好，阈值语义与无 mask 路径一致。
                    m = cv2.resize(mask, th.shape[1::-1],
                                   interpolation=cv2.INTER_AREA)
                    res = cv2.matchTemplate(work, th, cv2.TM_CCORR_NORMED,
                                            mask=np.where(m > 127, 255, 0)
                                            .astype(np.uint8))
                else:
                    res = cv2.matchTemplate(work, th, cv2.TM_CCOEFF_NORMED)
                _, mv, _, ml = cv2.minMaxLoc(res)
                if mv > best[0]:
                    best = (mv, ml[0], ml[1], scale)
            # 受限区间未达阈值 → 回退全 SCALES（数据声明尺度偏差兜底）
            if fallback_scales is not None and best[0] < self.threshold:
                for scale in fallback_scales:
                    th = cv2.resize(t, None, fx=scale, fy=scale,
                                    interpolation=cv2.INTER_AREA)
                    if th.shape[0] >= wh or th.shape[1] >= ww:
                        continue
                    if mask is not None:
                        m = cv2.resize(mask, th.shape[1::-1],
                                       interpolation=cv2.INTER_AREA)
                        res = cv2.matchTemplate(
                            work, th, cv2.TM_CCORR_NORMED,
                            mask=np.where(m > 127, 255, 0).astype(np.uint8))
                    else:
                        res = cv2.matchTemplate(work, th, cv2.TM_CCOEFF_NORMED)
                    _, mv, _, ml = cv2.minMaxLoc(res)
                    if mv > best[0]:
                        best = (mv, ml[0], ml[1], scale)
        except cv2.error as e:
            # OpenCV 失败显式处理（不静默吞/不裸崩）
            raise RuntimeError(f"模板匹配 OpenCV 错误: {e}") from e
        val, x, y, best_scale = best
        if val < self.threshold:
            return None
        # 工作空间坐标 → 全屏坐标（点击用绝对屏幕坐标）
        if work_scale < 1.0:
            x = int(x / work_scale)
            y = int(y / work_scale)
        # 用命中尺度的缩放模板尺寸算中心
        th_w = int(t.shape[1] * best_scale / work_scale)
        th_h = int(t.shape[0] * best_scale / work_scale)
        # 匹配耗时记录（定位哪一步慢）
        self.last_match_ms = round((time.time() - t0) * 1000, 1)
        return val, x + th_w // 2, y + th_h // 2

    def click_template(self, path, threshold=None, max_retries=3,
                       scale_range=None):
        th = threshold or self.threshold
        for attempt in range(max_retries):
            hit = self.locate(path, scale_range=scale_range)
            if hit is None:
                time.sleep(0.8)
                continue
            val, cx, cy = hit
            r = self.backend.click(cx, cy)
            if r.success:
                return r
            time.sleep(0.8)
        return None
