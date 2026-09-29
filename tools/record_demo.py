# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright", "pillow"]
# ///
"""录制 60 秒正片（1920×1080 / 25fps / 字幕烧入 / 自制配乐）。

为什么这么采集（照搬 ohbj 踩过的坑，一条都别再踩）：
  1. 不用 Playwright 的 `record_video_dir`：它的 VP8 固定只有 ~0.8 Mbps，1080p 的字与地图全糊。
     改用 CDP `Page.startScreencast`（JPEG q90）逐帧抓，按真实时间戳编码，源头质量高一个量级。
  2. Chrome 只在**画面变化**时发帧，静止段会有几秒空隙 → 必须把变帧率时间轴
     `resample` 到 25fps 均匀网格（用最近的前一帧填充），否则成片会被截短。
  3. ffmpeg 滤镜里的路径**不能带盘符冒号**（`C:` 会被当成选项分隔符）→ 统一在 video/ 目录下用相对路径。
  4. 字幕用 ASS 并**显式声明 PlayResX/Y = 1920/1080**；直接喂 SRT 会被 libass 按 288 放大 3.75 倍。

用法：
  uv run tools/record_demo.py                 # 录 + 出成片
  uv run tools/record_demo.py --probe         # 只自检交互（不录像），能省一次白录
  uv run tools/record_demo.py --list-only     # 只打印时间轴
  uv run tools/record_demo.py --keep-frames   # 保留采集帧，便于换 CRF 重编码
  uv run tools/record_demo.py --encode-only   # 复用已有帧重编码
  uv run tools/record_demo.py --headless      # 无头（默认有头，地图 WebGL 更稳）
"""
from __future__ import annotations

import argparse
import base64
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(errors="replace")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

W, H = 1920, 1080
FPS = 25
TOTAL_SEC = 60.0
TITLE_SEC = 4.0          # 片头标题卡
XFADE_SEC = 0.6          # 片头 → 实机 的交叉淡化
URL = "http://127.0.0.1:8123/"

FONTS = Path("C:/Windows/Fonts")
SKY = (10, 110, 189)
DANGER = (217, 48, 37)
GO = (15, 157, 88)
INK = (22, 32, 43)
INK2 = (90, 107, 125)

# 字幕（时间按"实机段"计，写到 ASS 时会自动加上片头时长）
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
    (54.2, "阳光充足 · 让每一趟都走得稳"),
]
TITLE_FRAMES = int(TITLE_SEC * FPS)
XFADE_FRAMES = int(XFADE_SEC * FPS)
LEAD = TITLE_FRAMES + XFADE_FRAMES      # 片头占用的总帧数


def font(name: str, size: int):
    p = FONTS / name
    if not p.exists():
        for alt in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf"):
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
    d.text((kx + 118, ky - 6), "阳光充足", font=font("msyhbd.ttc", 96), fill=(255, 255, 255), anchor="la")
    d.text((kx + 124, ky + 112), "基于气象与能耗预测的物流调度",
           font=font("msyh.ttc", 40), fill=(150, 180, 210), anchor="la")
    d.line([(kx + 124, ky + 176), (W - 200, ky + 176)], fill=(48, 78, 108), width=2)

    d.text((kx + 124, ky + 200), "沿程气象 × 低洼地形 → 积水风险场", font=font("msyh.ttc", 30), fill=(120, 200, 240), anchor="la")
    d.text((kx + 124, ky + 248), "坡度/风阻/暴雨加权 → 电量枯竭点", font=font("msyh.ttc", 30), fill=(120, 200, 240), anchor="la")
    d.text((kx + 124, ky + 296), "补给站与绕行 → 一次算出一条能走的路", font=font("msyh.ttc", 30), fill=(120, 200, 240), anchor="la")

    d.text((kx + 124, H - 190), "百度地图 · 开发者创作大赛 · 智能物流", font=font("msyh.ttc", 28), fill=(110, 140, 170), anchor="la")
    d.text((kx + 124, H - 148), "气象与能耗为模型预测，非实测；仅供调度参考",
           font=font("msyh.ttc", 24), fill=(190, 120, 110), anchor="la")

    p = outdir / "_title.png"
    img.save(p, "PNG")
    return p


# ────────────────────────── 驱动 ──────────────────────────
class Rec:
    def __init__(self, page, t0=None):
        self.page = page
        self.t0 = t0
        self.log: list[str] = []

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
          chosen: (document.querySelector('#cands .cand.on .tagid')||{}).textContent,
          risk: (document.getElementById('vKv')||{}).innerText || ''
        })""")


def timeline(rec: Rec, probe: bool = False):
    p = rec.page
    rec.set_scenario("s1-0800")
    rec.set_time(8.0)
    p.wait_for_timeout(400)
    rec.note("情景 s1-0800 · 08:00 出发")
    if probe:
        rec.note("自检：" + str(rec.state()))
        return

    rec.until(3.6)
    # 时间滑杆推进（雨带压向干线）
    for h in [8.0]:
        pass
    steps = [(4.0, 9.5), (6.0, 11.0), (8.0, 12.4), (10.0, 13.0), (12.0, 14.2), (14.6, 15.4)]
    for t_target, hour in steps:
        rec.until(t_target)
        rec.set_time(hour)
    rec.note("时间轴推进到 15:24")

    rec.until(18.0)
    rec.until(22.0)

    rec.until(25.0)
    rec.click_cand("A")
    rec.note("点开方案 A")

    rec.until(29.6)
    rec.click_cand("B")
    rec.note("点开方案 B")

    rec.until(40.0)
    rec.set_scenario("s2-0400")
    rec.set_time(4.0)
    rec.note("切到 04:00 情景")

    rec.until(44.5)
    rec.set_time(11.0)
    rec.until(48.0)
    rec.set_scenario("s3-heavy")
    rec.set_time(8.0)
    rec.note("切到满载重车情景")

    rec.until(TOTAL_SEC - LEAD / FPS - 0.4)


# ────────────────────────── 采集与编码 ──────────────────────────
def capture(outdir: Path, frames_dir: Path, headless: bool) -> list[tuple[float, Path]]:
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
        page.wait_for_timeout(3500)   # 等地图与首帧渲染

        client = ctx.new_cdp_session(page)

        def on_frame(params):
            try:
                data = base64.b64decode(params["data"])
                idx = len(frames)
                fp = frames_dir / f"{idx:05d}.jpg"
                fp.write_bytes(data)
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

    print(f"采集 {len(frames)} 帧（Chrome 只在画面变化时发帧，故远少于 25fps×{TOTAL_SEC:.0f}s）")
    return frames


def resample(frames: list[tuple[float, Path]], seq_dir: Path) -> int:
    """把变帧率时间轴重采样到 25fps 均匀网格（用最近的前一帧填充）。"""
    if not frames:
        raise SystemExit("没有采集到任何帧")
    if seq_dir.exists():
        shutil.rmtree(seq_dir)
    seq_dir.mkdir(parents=True, exist_ok=True)

    t0 = frames[0][0]
    total = TOTAL_SEC
    n_frames = int(round(total * FPS)) - LEAD
    cur = 0
    for i in range(n_frames):
        target = i / FPS
        while cur + 1 < len(frames) and frames[cur + 1][0] - t0 <= target:
            cur += 1
        dst = seq_dir / f"{LEAD + i + 1:05d}.jpg"
        try:
            os.link(frames[cur][1], dst)
        except OSError:
            shutil.copy2(frames[cur][1], dst)
    print(f"重采样 → {n_frames} 帧实机画面 + 片头 {LEAD} 帧 = {total:.1f}s")
    return n_frames


def build_lead(seq_dir: Path, title: Path):
    """片头帧 + 与实机首帧的交叉淡化。"""
    t = Image.open(title).convert("RGB")
    first = Image.open(seq_dir / f"{LEAD + 1:05d}.jpg").convert("RGB").resize((W, H), Image.LANCZOS)
    for i in range(TITLE_FRAMES):
        f = t.copy()
        if i < 8:                       # 淡入
            a = i / 8
            f = Image.blend(Image.new("RGB", (W, H), (10, 16, 24)), f, a)
        f.save(seq_dir / f"{i + 1:05d}.jpg", "JPEG", quality=95)
    for i in range(XFADE_FRAMES):
        a = i / XFADE_FRAMES
        Image.blend(t, first, a).save(seq_dir / f"{TITLE_FRAMES + i + 1:05d}.jpg", "JPEG", quality=95)


def write_ass(out: Path, subs: list[tuple[float, str]], size: int = 46, offset: float = 0.0):
    def ts(sec: float) -> str:
        sec = max(0.0, sec)
        h = int(sec // 3600); m = int(sec % 3600 // 60); s = sec % 60
        return f"{h:d}:{m:02d}:{s:05.2f}"
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Def,Microsoft YaHei,{size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,2.2,1.4,2,120,120,56,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    n = len(subs)
    for i, (t, text) in enumerate(subs):
        end = subs[i + 1][0] - 0.15 if i + 1 < n else t + 3.4
        lines.append(f"Dialogue: 0,{ts(t + offset)},{ts(end + offset)},Def,,0,0,0,,{text}")
    out.write_text(head + "\n".join(lines) + "\n", encoding="utf-8-sig")


def encode(video_dir: Path, seq_dir: Path, crf: int):
    music = video_dir / "music.wav"
    if not music.exists():
        print("生成配乐（自制合成，无版权顾虑）…")
        subprocess.run(["uv", "run", str(Path("tools/make_music.py")), "--seconds", "64",
                        "--out", str(music)], check=True)
    out = video_dir / "正片-阳光充足.mp4"
    cmd = [
        "ffmpeg", "-y", "-framerate", str(FPS), "-i", "_seq/%05d.jpg",
        "-i", "music.wav",
        "-filter_complex", "[0:v]ass=subs.ass[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart",
        out.name,
    ]
    subprocess.run(cmd, cwd=video_dir, check=True)
    size = out.stat().st_size / 1024 / 1024
    print(f"\n成片：{out}  {size:.1f} MB")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="只自检交互，不录像")
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--keep-frames", action="store_true")
    ap.add_argument("--encode-only", action="store_true")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--crf", type=int, default=15)
    args = ap.parse_args()

    if args.list_only:
        print(f"总长 {TOTAL_SEC:.0f}s ＝ 片头 {TITLE_SEC:.1f}s + 实机 {TOTAL_SEC - TITLE_SEC - XFADE_SEC:.1f}s")
        for t, s in SUBS:
            print(f"  {TITLE_SEC + XFADE_SEC + t:6.1f}s  {s}")
        return 0

    video_dir = Path("video")
    video_dir.mkdir(exist_ok=True)
    seq_dir = video_dir / "_seq"

    if args.probe:
        from playwright.sync_api import sync_playwright
        print("自检交互（不录像）：")
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True, executable_path=find_chromium())
            pg = b.new_context(viewport={"width": W, "height": H}).new_page()
            pg.goto(URL, wait_until="load")
            pg.wait_for_timeout(3500)
            rec = Rec(pg, 0)
            for tag in ("A", "B", "C"):
                print(f"  点开方案 {tag}: {rec.click_cand(tag)}")
            pg.evaluate("() => document.getElementById('scenario').value")
            print("  " + str(rec.state()))
            b.close()
        return 0

    if not args.encode_only:
        title = build_title(video_dir)
        print(f"片头卡：{title}")
        frames = capture(video_dir, video_dir / "_frames", args.headless)
        resample(frames, seq_dir)
        build_lead(seq_dir, title)

    write_ass(video_dir / "subs.ass", SUBS, 46, TITLE_SEC + XFADE_SEC)
    print("字幕：video/subs.ass（PlayResX/Y=1920/1080，字号即像素）")
    encode(video_dir, seq_dir, args.crf)

    if not args.keep_frames:
        shutil.rmtree(video_dir / "_frames", ignore_errors=True)
        shutil.rmtree(seq_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
