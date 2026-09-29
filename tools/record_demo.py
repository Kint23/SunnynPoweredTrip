# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright", "pillow"]
# ///
"""录制 60 秒**讲解版**正片（1920×1080 / 25fps / 字幕加大 / 放大 + 圈画 / 自制配乐）。

和上一版的区别（用户反馈"实操为主、讲解作用太弱"）：
  1. **放大到响应部位**：每个讲解段落把相关区域（地图 / 剖面图 / 某张候选卡 / 某一行数字）
     平滑放大到满屏，过渡用缓动，不是硬切。
  2. **圈画效果**：用百度地图的 pointToPixel 把"高风险段中点""电量告警点"的真实屏幕坐标取出来，
     在放大后的画面上套一圈琥珀色描边，并在顶部压一条说明条。
  3. **字幕加大**：ASS 字号 46 → 60，并改用半透明底框（BorderStyle=3），压在放大画面上也读得清。

采集仍走 CDP `Page.startScreencast`（JPEG q92）—— Playwright 自带的 VP8 只有 ~0.8 Mbps，
1080p 的字与地图会被压糊。Chrome 只在画面变化时发帧，所以必须把变帧率时间轴
重采样到 25fps 均匀网格，否则成片会被截短。

用法：
  uv run tools/record_demo.py                 # 录 + 出成片
  uv run tools/record_demo.py --probe         # 只自检交互（不录像）
  uv run tools/record_demo.py --list-only     # 只打印时间轴与镜头表
  uv run tools/record_demo.py --encode-only   # 复用已有帧重编码
  uv run tools/record_demo.py --no-zoom       # 关掉放大/圈画，回到纯实操版
  uv run tools/record_demo.py --headless
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(errors="replace")

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont  # noqa: E402

W, H = 1920, 1080
FPS = 25
TOTAL_SEC = 60.0
TITLE_SEC = 4.0
XFADE_SEC = 0.6
URL = "http://127.0.0.1:8123/"

FONTS = Path("C:/Windows/Fonts")
SKY = (10, 110, 189)
AMBER = (255, 176, 32)
INK = (22, 32, 43)

# ── 字幕（时间按"实机段"计，写 ASS 时自动加片头时长）──────────────────
SUBS: list[tuple[float, str]] = [
    (0.5, "西安 → 成都，690 公里，49 吨电动重卡"),
    (4.6, "沿路线取点，拉取未来 24 小时逐小时气象"),
    (8.0, "按预计到达时刻插值，得到「沿程天气带」"),
    (11.4, "46 mm/h 暴雨落在广元河谷低洼段"),
    (14.6, "降水 × 低洼闸门 → 积水风险 1.00"),
    (18.2, "秦岭爬升 1379 m，下坡按 60% 效率回收"),
    (21.6, "电量在 K320 触及 30% 告警线"),
    (25.0, "方案 A：就地补能，不绕行"),
    (29.6, "方案 B：绕开积水段，走并行国省道"),
    (33.4, "风险暴露 51.7 → 3.7 km·r"),
    (36.6, "代价只有 0.7 公里、17 分钟"),
    (40.6, "同样的车，早出发 4 小时"),
    (44.0, "雨带还没起来：无高风险段，风险暴露仅 15"),
    (48.0, "重载 + 低电量：模型直接说「这趟跑不完」"),
    (51.6, "不编一个方案出来 —— 这才是可执行的调度"),
    (54.2, "晴好出发 · 让每一趟都走得稳"),
]

# ── 镜头表：把哪个区域放大 + 圈什么 ──────────────────────────────────
# (t0, t1, focus, 说明条文字, 圈画, 模式)
#   focus : 布局快照里的键（map / profile / cand_A / card_decision …）
#   圈画  : None | ("pt", "hazard") | ("sel", "kv_last") | ("rect", (nx,ny,nw,nh))
#   模式  : 'zoom' = 该区域放大到满屏；'pip' = 区域做成画中画、其余压暗
#           （侧栏只有 372px 宽，硬拉满屏会只剩一片地图，所以侧栏一律用 pip）
SEGMENTS: list[tuple] = [
    (0.0, 4.4, None, None, None, None),
    (4.6, 11.2, "map", "① 沿路线取点 · 未来 24 小时逐小时", None, "zoom"),
    (11.4, 17.8, "map", "② 暴雨落在低洼河谷 → 高风险积水段", ("pt", "hazard"), "zoom"),
    (18.0, 24.6, "profile", "③ 沿里程剖面：海拔 / 降水 / 风险 / 电量 SOC",
     ("rect", (0.60, 0.05, 0.22, 0.93)), "zoom"),
    (24.8, 29.4, "cand_A", "④ 方案 A：就地补能，不绕行", None, "pip"),
    (29.6, 33.2, "cand_B", "⑤ 方案 B：绕开积水段（并行国省道）", None, "pip"),
    (33.4, 40.2, "card_decision", "⑥ 风险暴露 51.7 → 3.7 km·r", ("sel", "kv_last"), "pip"),
    (40.6, 47.6, "card_decision", "⑦ 早出发 4 小时 → 无高风险段", ("sel", "kv_last"), "pip"),
    (48.0, 55.4, "card_decision", "⑧ 不可行就说不 —— 不编一个方案出来", ("sel", "verdict"), "pip"),
]

TITLE_FRAMES = int(TITLE_SEC * FPS)
XFADE_FRAMES = int(XFADE_SEC * FPS)
LEAD = TITLE_FRAMES + XFADE_FRAMES
LEAD_SEC = LEAD / FPS
RAMP_IN, RAMP_OUT = 9, 7        # 放大/缩小的缓动帧数
MAX_ZOOM = 3.0
MIN_ZOOM = 1.8                  # 地图/剖面图跟整帧一样宽，不限一下根本不会真的放大


def font(name: str, size: int):
    p = FONTS / name
    if not p.exists():
        for alt in ("msyhbd.ttc", "msyh.ttc", "simhei.ttf"):
            q = FONTS / alt
            if q.exists():
                return ImageFont.truetype(str(q), size)
        raise SystemExit("找不到中文字体")
    return ImageFont.truetype(str(p), size)


def find_chromium() -> str | None:
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if not base.exists():
        return None
    for d in sorted((x for x in base.glob("chromium-*") if x.is_dir()), reverse=True):
        for exe in ("chrome-win64/chrome.exe", "chrome-win/chrome.exe"):
            p = d / exe
            if p.exists():
                return str(p)
    return None


# ────────────────────────── 片头 ──────────────────────────
def build_title(outdir: Path) -> Path:
    img = Image.new("RGB", (W, H), (10, 16, 24))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(12 + 8 * t), int(22 + 14 * t), int(34 + 22 * t)))
    for x in range(0, W, 60):
        d.line([(x, 0), (x, H)], fill=(18, 30, 44))
    for y in range(0, H, 60):
        d.line([(0, y), (W, y)], fill=(18, 30, 44))
    d.rectangle([0, 0, W, 6], fill=SKY)

    kx, ky = 200, 392
    d.rounded_rectangle([kx, ky, kx + 84, ky + 84], radius=24, fill=SKY)
    d.text((kx + 42, ky + 44), "晴", font=font("msyhbd.ttc", 46), fill=(255, 255, 255), anchor="mm")
    d.text((kx + 118, ky - 6), "晴好出发", font=font("msyhbd.ttc", 96), fill=(255, 255, 255), anchor="la")
    d.text((kx + 124, ky + 112), "基于气象与能耗预测的物流调度",
           font=font("msyh.ttc", 40), fill=(150, 180, 210), anchor="la")
    d.line([(kx + 124, ky + 176), (W - 200, ky + 176)], fill=(48, 78, 108), width=2)

    for i, t in enumerate(["沿程气象 × 低洼地形 → 积水风险场",
                           "坡度/风阻/暴雨加权 → 电量枯竭点",
                           "补给站与绕行 → 一次算出一条能走的路"]):
        d.text((kx + 124, ky + 200 + i * 48), t, font=font("msyh.ttc", 30), fill=(120, 200, 240), anchor="la")
    d.text((kx + 124, H - 190), "百度地图 · 开发者创作大赛 · 智能物流",
           font=font("msyh.ttc", 28), fill=(110, 140, 170), anchor="la")
    d.text((kx + 124, H - 148), "气象与能耗为模型预测，非实测；仅供调度参考",
           font=font("msyh.ttc", 24), fill=(190, 120, 110), anchor="la")

    p = outdir / "_title.png"
    img.save(p, "PNG")
    return p


# ────────────────────────── 布局快照 ──────────────────────────
SNAP_JS = """() => {
  const R = (el) => { if (!el) return null; const b = el.getBoundingClientRect();
    return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]; };
  const Q = (s) => R(document.querySelector(s));
  const out = { cands: {}, pts: {} };
  out.map = Q('.stage'); out.profile = Q('.profile'); out.sidebar = Q('.sidebar');
  out.verdict = Q('.verdict'); out.kv = Q('#vKv'); out.stations = Q('#stations');
  out.why = Q('#why'); out.timebar = Q('.timebar'); out.clock = Q('#clock');
  [...document.querySelectorAll('#cands .cand')].forEach((e, i) => {
    const tag = (e.querySelector('.tagid') || {}).textContent || String(i + 1);
    out.cands[tag] = R(e);
  });
  const dts = [...document.querySelectorAll('#vKv dt')], dds = [...document.querySelectorAll('#vKv dd')];
  if (dts.length && dds.length) {
    const a = dts[dts.length - 1].getBoundingClientRect();
    const c = dds[dds.length - 1].getBoundingClientRect();
    const x = Math.min(a.x, c.x), r = Math.max(a.right, c.right);
    out.kv_last = [Math.round(x), Math.round(a.y), Math.round(r - x), Math.round(Math.max(a.height, c.height))];
  }
  // 侧栏那几个「卡片」的外框：侧栏宽只有 372px，放大时用整张卡片做画中画才好看
  const card = (sel) => { const e = document.querySelector(sel); return e ? R(e.closest('.panel')) : null; };
  out.card_decision = card('#vKv'); out.card_cands = card('#cands'); out.card_stations = card('#stations');
  out.card_why = card('#why');
  // 用百度地图投影，把「高风险段中点 / 电量告警点」换成屏幕坐标，好在讲解时圈出来
  try {
    const S = window.__SPT, mapEl = document.querySelector('#bmap');
    const mr = mapEl.getBoundingClientRect();
    const toScreen = (bd) => {
      const p = S.map.pointToPixel(new BMapGL.Point(bd[0], bd[1]));
      return [Math.round(mr.x + p.x), Math.round(mr.y + p.y)];
    };
    const nearest = (km) => S.res.base_points.reduce(
      (a, p) => Math.abs(p.km - km) < Math.abs(a.km - km) ? p : a);
    const h = S.res.base.hazard[0];
    if (h && S.map) out.pts.hazard = toScreen(nearest((h.km0 + h.km1) / 2).bd);
    if (S.map && S.res.stations.warn_km != null) out.pts.warn = toScreen(nearest(S.res.stations.warn_km).bd);
  } catch (e) { /* 离线示意底图没有 BMapGL，忽略 */ }
  return out;
}"""


class Rec:
    def __init__(self, page, t0=None):
        self.page = page
        self.t0 = t0
        self.log: list[str] = []
        self.snaps: list[tuple[float, dict]] = []

    def start(self):
        self.t0 = time.monotonic()
        return self

    @property
    def now(self) -> float:
        return time.monotonic() - self.t0

    def until(self, t: float):
        dt = t - self.now
        if dt > 0:
            self.page.wait_for_timeout(int(dt * 1000))

    def note(self, s: str):
        self.log.append(f"{self.now:6.1f}s  {s}")
        print(f"{self.now:6.1f}s  {s}", flush=True)

    def js(self, code, arg=None):
        try:
            return self.page.evaluate(code, arg) if arg is not None else self.page.evaluate(code)
        except Exception as e:  # noqa: BLE001
            self.note(f"! js 失败: {e}")
            return None

    def snap(self, label: str):
        """记录当前布局（各控件的屏幕矩形 + 关键点坐标），供放大/圈画使用。"""
        d = self.js(SNAP_JS)
        if isinstance(d, dict):
            self.snaps.append((self.now, d))
            got = [k for k in ("map", "profile", "kv", "kv_last", "verdict") if d.get(k)]
            got += [f"cand_{k}" for k in d.get("cands", {})]
            self.note(f"布局快照[{label}] {', '.join(got)} | pts={list(d.get('pts', {}))}")
        else:
            self.note(f"! 布局快照[{label}] 失败")

    def set_time(self, hour: float):
        self.js("h => { const e=document.getElementById('time'); e.value=String(h);"
                " e.dispatchEvent(new Event('input',{bubbles:true})); }", hour)

    def set_scenario(self, sid: str):
        self.js("s => { const e=document.getElementById('scenario'); e.value=s;"
                " e.dispatchEvent(new Event('change',{bubbles:true})); }", sid)

    def click_cand(self, tag: str) -> bool:
        return bool(self.js("""t => {
          const els = [...document.querySelectorAll('#cands .cand')];
          const el = els.find(e => (e.querySelector('.tagid')||{}).textContent === t);
          if (el) { el.click(); return true; } return false;
        }""", tag))

    def state(self):
        return self.js("""() => ({
          tag: (document.getElementById('vTag')||{}).textContent,
          scen: (document.getElementById('scenario')||{}).value,
          chosen: (document.querySelector('#cands .cand.on .tagid')||{}).textContent
        })""")


def timeline(rec: Rec):
    p = rec.page
    rec.set_scenario("s1-0800")
    rec.set_time(8.0)
    p.wait_for_timeout(500)
    rec.note("情景 s1-0800 · 08:00 出发")
    rec.snap("s1-start")

    rec.until(3.8)
    for t_target, hour in [(4.0, 9.5), (6.0, 11.0), (8.0, 12.4), (10.0, 13.0),
                           (12.0, 14.2), (14.6, 15.4)]:
        rec.until(t_target)
        rec.set_time(hour)
    rec.snap("s1-hazard")            # 此时高风险段已出现，能取到 hazard 屏幕坐标
    rec.note("时间轴推进到 15:24（雨带压向干线）")

    rec.until(25.0)
    rec.click_cand("A")
    rec.note("点开方案 A")
    rec.snap("s1-candA")

    rec.until(29.6)
    rec.click_cand("B")
    rec.note("点开方案 B")
    rec.snap("s1-candB")

    rec.until(40.0)
    rec.set_scenario("s2-0400")
    rec.set_time(4.0)
    rec.note("切到 04:00 情景")
    p.wait_for_timeout(700)
    rec.snap("s2")

    rec.until(44.5)
    rec.set_time(11.0)
    rec.until(48.0)
    rec.set_scenario("s3-heavy")
    rec.set_time(8.0)
    p.wait_for_timeout(700)
    rec.note("切到满载重车情景")
    rec.snap("s3")

    rec.until(TOTAL_SEC - LEAD_SEC - 0.4)


# ────────────────────────── 采集 ──────────────────────────
def capture(frames_dir: Path, headless: bool, layout_path: Path) -> list[tuple[float, Path]]:
    from playwright.sync_api import sync_playwright

    frames: list[tuple[float, Path]] = []
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        exe = find_chromium()
        browser = pw.chromium.launch(headless=headless, executable_path=exe,
                                     args=["--window-size=1920,1120", "--force-device-scale-factor=1",
                                           "--disable-lcd-text", "--hide-scrollbars"])
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1)
        page = ctx.new_page()
        page.goto(URL, wait_until="load")
        page.wait_for_timeout(4000)

        client = ctx.new_cdp_session(page)

        def on_frame(params):
            try:
                idx = len(frames)
                fp = frames_dir / f"{idx:05d}.jpg"
                fp.write_bytes(base64.b64decode(params["data"]))
                frames.append((time.monotonic(), fp))
                client.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            except Exception as e:  # noqa: BLE001
                print("! 帧处理失败:", e, flush=True)

        client.on("Page.screencastFrame", on_frame)
        client.send("Page.startScreencast", {
            "format": "jpeg", "quality": 92, "maxWidth": W, "maxHeight": H, "everyNthFrame": 1
        })

        rec = Rec(page).start()
        timeline(rec)
        client.send("Page.stopScreencast")
        browser.close()

    layout_path.write_text(json.dumps(rec.snaps, ensure_ascii=False), encoding="utf-8")
    print(f"采集 {len(frames)} 帧 ｜ 布局快照 {len(rec.snaps)} 份 → {layout_path}")
    return frames


def resample(frames: list[tuple[float, Path]], seq_dir: Path) -> int:
    """把变帧率时间轴重采样到 25fps 均匀网格（用最近的前一帧填充）。"""
    if not frames:
        raise SystemExit("没有采集到任何帧")
    if seq_dir.exists():
        shutil.rmtree(seq_dir)
    seq_dir.mkdir(parents=True, exist_ok=True)

    t0 = frames[0][0]
    n = int(round(TOTAL_SEC * FPS)) - LEAD
    cur = 0
    for i in range(n):
        target = i / FPS
        while cur + 1 < len(frames) and frames[cur + 1][0] - t0 <= target:
            cur += 1
        dst = seq_dir / f"{LEAD + i + 1:05d}.jpg"
        try:
            os.link(frames[cur][1], dst)
        except OSError:
            shutil.copy2(frames[cur][1], dst)
    print(f"重采样 → {n} 帧实机画面 + 片头 {LEAD} 帧 = {TOTAL_SEC:.1f}s")
    return n


# ────────────────────────── 放大 + 圈画 ──────────────────────────
def window_for(rect, fill=0.86, max_zoom=MAX_ZOOM, min_zoom=MIN_ZOOM):
    """围绕 focus 取一个 16:9 的窗口（源帧坐标）。

    - fill：focus 要占窗口的多少比例（保证主体完整）；
    - min_zoom：窗口最大只能到 W/min_zoom —— 否则像地图（宽 1512）这种区域
      会算出 1900+ 的窗口、被裁到整帧宽度，结果“放大”等于没放大。
    """
    x, y, w, h = rect
    cx, cy = x + w / 2, y + h / 2
    win_w = max(w / fill, (h / fill) * 16 / 9, W / max_zoom)
    win_w = min(win_w, W / min_zoom, W)
    win_h = win_w * 9 / 16
    if win_h > H:
        win_h, win_w = H, H * 16 / 9
    wx = min(max(0.0, cx - win_w / 2), W - win_w)
    wy = min(max(0.0, cy - win_h / 2), H - win_h)
    return [wx, wy, win_w, win_h]


def lerp(a, b, t):
    return a + (b - a) * t


def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def seg_at(t: float):
    for s in SEGMENTS:
        if s[0] <= t < s[1]:
            return s
    return None


def ann_box(layout, ann, focus):
    """把圈画描述算成源帧坐标的矩形；点类返回 None（由调用方画圆）。"""
    if not ann:
        return None
    kind, val = ann
    if kind == "sel":
        return layout.get(val)
    if kind == "rect":
        nx, ny, nw, nh = val
        return [focus[0] + nx * focus[2], focus[1] + ny * focus[3], nw * focus[2], nh * focus[3]]
    return None


def pick_layout(snaps, t):
    best = None
    for st, d in snaps:
        if st <= t:
            best = d
    return best or (snaps[0][1] if snaps else {})


def focus_rect(layout, key):
    if not layout:
        return None
    if key.startswith("cand_"):
        return layout.get("cands", {}).get(key[5:])
    return layout.get(key)


def chip(d: ImageDraw.ImageDraw, text: str, alpha: float):
    f = font("msyhbd.ttc", 34)
    tw = d.textlength(text, font=f)
    pad_x, pad_y = 26, 15
    bw, bh = tw + pad_x * 2, 34 + pad_y * 2
    x, y = (W - bw) / 2, 26
    a = int(210 * alpha)
    d.rounded_rectangle([x + 3, y + 4, x + bw + 3, y + bh + 4], radius=bh / 2,
                        fill=(0, 0, 0, int(90 * alpha)))
    d.rounded_rectangle([x, y, x + bw, y + bh], radius=bh / 2, fill=SKY + (a,))
    d.text((x + bw / 2, y + bh / 2 + 1), text, font=f, fill=(255, 255, 255, int(255 * alpha)), anchor="mm")


def ring(d: ImageDraw.ImageDraw, box, alpha: float, ellipse: bool):
    x0, y0, x1, y1 = box
    glow = AMBER + (int(70 * alpha),)
    solid = AMBER + (int(245 * alpha),)
    for w, col in ((18, glow), (6, solid)):
        if ellipse:
            d.ellipse([x0 - w / 2, y0 - w / 2, x1 + w / 2, y1 + w / 2], outline=col, width=w)
        else:
            d.rounded_rectangle([x0 - w / 2, y0 - w / 2, x1 + w / 2, y1 + w / 2],
                                radius=18, outline=col, width=w)


def compose(seq_dir: Path, n: int, snaps, enabled: bool = True):
    """把每个讲解段落的相关区域平滑放大到满屏，并圈出重点。"""
    if not enabled:
        print("已跳过放大/圈画（--no-zoom）")
        return
    done = 0
    last_key, last_out = None, None
    for i in range(n):
        t = i / FPS
        seg = seg_at(t)
        if not seg or not seg[2]:
            continue
        t0, t1, key, note, ann, mode = seg
        layout = pick_layout(snaps, t0)
        rect = focus_rect(layout, key)
        if not rect:
            continue
        pin = ease((t - t0) / (RAMP_IN / FPS))
        pout = ease((t1 - t) / (RAMP_OUT / FPS))
        p = min(pin, pout)
        if mode == "pip":
            win = [0.0, 0.0, float(W), float(H)]          # pip 不动源窗口，只动面板
        else:
            target = window_for(rect)
            win = [lerp(0, target[j], p) for j in range(4)]

        fp = seq_dir / f"{LEAD + i + 1:05d}.jpg"
        # 录屏里大量相邻帧是同一张源图（重采样时是**硬链接**）。
        # ⚠️ 绝对不能在原路径上直接写：Image.save 会截断同一个 inode，
        #    "修改一帧"会连带改掉所有指向它的兄弟帧。所以写之前必须解链。
        try:
            ino = os.stat(fp).st_ino
        except OSError:
            ino = None
        sig = (mode, int(round(p * 100)), ino) if mode == "pip" else \
              (mode, int(round(win[0])), int(round(win[1])), int(round(win[2])), int(round(win[3])), ino)
        if sig == last_key and last_out is not None and last_out.exists():
            fp.unlink()
            shutil.copy2(last_out, fp)
            done += 1
            continue
        if p <= 0.02:            # 还没开始放大：保持原帧，直接跳过（不做无谓解码）
            continue

        img = Image.open(fp).convert("RGB")
        if mode == "pip":
            out, ox, oy, sc = pip_frame(img, rect, p)
        else:
            x, y, w, h = [int(round(v)) for v in win]
            x, y = max(0, min(x, W - w)), max(0, min(y, H - h))
            w, h = min(w, W - x), min(h, H - y)
            out = img.crop((x, y, x + w, y + h)).resize((W, H), Image.LANCZOS)
            sc = W / w
            ox, oy = -x * sc, -y * sc
        d = ImageDraw.Draw(out, "RGBA")
        # 圈画：统一用 屏幕坐标 = 源坐标 × sc + (ox, oy)
        if ann and ann[0] == "pt":
            pt = (layout.get("pts") or {}).get(ann[1])
            if pt:
                cx, cy = pt[0] * sc + ox, pt[1] * sc + oy
                r = 104 * p
                ring(d, (cx - r, cy - r, cx + r, cy + r), p, True)
        elif ann:
            bx = ann_box(layout, ann, rect)
            if bx:
                ax, ay = bx[0] * sc + ox, bx[1] * sc + oy
                aw, ah = bx[2] * sc, bx[3] * sc
                ring(d, (ax, ay, ax + aw, ay + ah), p, False)
        if note:
            chip(d, note, p)
        fp.unlink()                      # 解链：让这一格拿到自己的新 inode
        out.save(fp, "JPEG", quality=95)
        last_key, last_out = sig, fp
        done += 1
    print(f"放大+圈画：处理 {done} 帧（共 {n} 帧）")


def pip_frame(img: Image.Image, rect, p: float):
    """画中画：背景全帧模糊压暗，前景是 focus 区域。返回 (画面, 偏移x, 偏移y, 缩放)。"""
    bg = img.filter(ImageFilter.GaussianBlur(18))
    bg = ImageEnhance.Brightness(bg).enhance(0.30)
    rx, ry, rw, rh = rect
    scale = (0.94 + 0.06 * p)
    pw = min(int(W * 0.72), int(rw * 3.2 * scale))
    ph = int(pw * rh / rw)
    if ph > H * 0.80:
        ph = int(H * 0.80)
        pw = int(ph * rw / rh)
    panel = img.crop((rx, ry, rx + rw, ry + rh)).resize((pw, ph), Image.LANCZOS)
    px, py = (W - pw) // 2, (H - ph) // 2 - 42
    d0 = ImageDraw.Draw(bg, "RGBA")
    d0.rounded_rectangle([px - 6, py - 6, px + pw + 6, py + ph + 6], radius=14, fill=(255, 255, 255, 22))
    bg.paste(panel, (px, py))
    d0.rounded_rectangle([px - 2, py - 2, px + pw + 2, py + ph + 2], radius=10,
                         outline=(255, 255, 255, int(150 * p)), width=2)
    sc = pw / rw
    return bg, px - rx * sc, py - ry * sc, sc


def build_lead(seq_dir: Path, title: Path):
    t = Image.open(title).convert("RGB")
    first = Image.open(seq_dir / f"{LEAD + 1:05d}.jpg").convert("RGB").resize((W, H), Image.LANCZOS)
    for i in range(TITLE_FRAMES):
        f = t.copy()
        if i < 8:
            f = Image.blend(Image.new("RGB", (W, H), (10, 16, 24)), f, i / 8)
        f.save(seq_dir / f"{i + 1:05d}.jpg", "JPEG", quality=95)
    for i in range(XFADE_FRAMES):
        Image.blend(t, first, i / XFADE_FRAMES).save(seq_dir / f"{TITLE_FRAMES + i + 1:05d}.jpg",
                                                     "JPEG", quality=95)


def write_ass(out: Path, subs, size: int = 60, offset: float = 0.0):
    def ts(sec: float) -> str:
        sec = max(0.0, sec)
        hh = int(sec // 3600); mm = int(sec % 3600 // 60); ss = sec % 60
        return f"{hh:d}:{mm:02d}:{ss:05.2f}"

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Def,Microsoft YaHei,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&HB4000000,0,0,0,0,100,100,0,0,3,12,0,2,150,150,72,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for i, (t, text) in enumerate(subs):
        end = subs[i + 1][0] - 0.15 if i + 1 < len(subs) else t + 3.4
        lines.append(f"Dialogue: 0,{ts(t + offset)},{ts(end + offset)},Def,,0,0,0,,{text}")
    out.write_text(head + "\n".join(lines) + "\n", encoding="utf-8-sig")


def encode(video_dir: Path, crf: int):
    music = video_dir / "music.wav"
    if not music.exists():
        print("生成配乐（自制合成，无版权顾虑）…")
        subprocess.run(["uv", "run", "tools/make_music.py", "--seconds", "64", "--out", str(music)],
                       check=True)
    out = video_dir / "正片-晴好出发.mp4"
    cmd = ["ffmpeg", "-y", "-framerate", str(FPS), "-i", "_seq/%05d.jpg", "-i", "music.wav",
           "-filter_complex", "[0:v]ass=subs.ass[v]", "-map", "[v]", "-map", "1:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out.name]
    subprocess.run(cmd, cwd=video_dir, check=True)
    print(f"\n成片：{out}  {out.stat().st_size / 1024 / 1024:.1f} MB")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--keep-frames", action="store_true")
    ap.add_argument("--encode-only", action="store_true")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--no-zoom", action="store_true", help="关掉放大/圈画")
    ap.add_argument("--crf", type=int, default=15)
    args = ap.parse_args()

    if args.list_only:
        print(f"总长 {TOTAL_SEC:.0f}s ＝ 片头 {TITLE_SEC:.1f}s + 实机 {TOTAL_SEC - TITLE_SEC - XFADE_SEC:.1f}s")
        print("镜头表（时间已含片头，`—` 表示不放大）：")
        for t0, t1, key, note, ann, mode in SEGMENTS:
            an = "" if not ann else ("圈点" if ann[0] == "pt" else ("框选" if ann[0] == "sel" else "框选"))
            print(f"  {t0 + LEAD_SEC:6.1f}–{t1 + LEAD_SEC:6.1f}s  {key or '—':14s} {mode or '—':5s} {an:5s} {note or ''}")
        return 0

    video_dir = Path("video")
    video_dir.mkdir(exist_ok=True)
    seq_dir = video_dir / "_seq"
    layout_path = video_dir / "_layout.json"

    if args.probe:
        from playwright.sync_api import sync_playwright
        print("自检交互（不录像）：")
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True, executable_path=find_chromium())
            pg = b.new_context(viewport={"width": W, "height": H}).new_page()
            pg.goto(URL, wait_until="load")
            pg.wait_for_timeout(4000)
            rec = Rec(pg, 0)
            for tag in ("A", "B", "C"):
                print(f"  点开方案 {tag}: {rec.click_cand(tag)}")
            rec.snap("probe")
            print("  state:", rec.state())
            b.close()
        return 0

    if not args.encode_only:
        title = build_title(video_dir)
        print(f"片头卡：{title}")
        frames = capture(video_dir / "_frames", args.headless, layout_path)
        n = resample(frames, seq_dir)
        snaps = json.loads(layout_path.read_text(encoding="utf-8")) if layout_path.exists() else []
        compose(seq_dir, n, snaps, enabled=not args.no_zoom)
        build_lead(seq_dir, title)

    write_ass(video_dir / "subs.ass", SUBS, 60, LEAD_SEC)
    print("字幕：video/subs.ass（字号 60 / PlayResX-Y=1920-1080 / 半透明底框）")
    encode(video_dir, args.crf)

    if not args.keep_frames:
        shutil.rmtree(video_dir / "_frames", ignore_errors=True)
        shutil.rmtree(seq_dir, ignore_errors=True)
        layout_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
