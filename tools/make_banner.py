# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""生成参赛 Banner（1200×675，PNG + JPG，≤5MB）。

设计原则（照搬 ohbj 的经验）：
  **文字与数字一律用 Pillow 绘制**，AI 只负责底纹 —— 这样标题、数字、坐标
  永远不会被模型"画出错字"。而且这里连地图也是**从 data/ 真实数据画的**，
  所以 Banner 和页面里的几何永远一致，不会"图文不符"。

用法：
  uv run tools/make_banner.py
  uv run tools/make_banner.py --bg banner/ai-bg-a.png --bg-mix 0.72
  uv run tools/make_banner.py --claim "避雨 + 补能，一次算出一条能走的路"
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 675
FONTS = Path("C:/Windows/Fonts")

SKY = (10, 110, 189)
SKY_D = (7, 81, 137)
SKY_L = (232, 242, 251)
DANGER = (217, 48, 37)
GO = (15, 157, 88)
INK = (22, 32, 43)
INK2 = (90, 107, 125)
INK3 = (139, 154, 168)
LINE = (221, 229, 236)
PAPER = (244, 247, 250)
WARN = (232, 163, 61)


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    p = FONTS / name
    if not p.exists():
        for alt in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc", "simkai.ttf"):
            q = FONTS / alt
            if q.exists():
                return ImageFont.truetype(str(q), size)
        raise SystemExit(f"找不到可用中文字体：{p}")
    return ImageFont.truetype(str(p), size)


def txt_w(d: ImageDraw.ImageDraw, s: str, f: ImageFont.FreeTypeFont, gap: float = 0.0) -> float:
    if not s:
        return 0.0
    return sum(d.textlength(c, font=f) for c in s) + gap * (len(s) - 1)


def draw_spaced(d, xy, s, f, fill, gap=0.0):
    x, y = xy
    for c in s:
        d.text((x, y), c, font=f, fill=fill, anchor="la")
        x += d.textlength(c, font=f) + gap
    return x


# ── 数据（与页面同源） ──────────────────────────────────────────
def load_geo():
    poly = json.loads(Path("data/corridors.json").read_text(encoding="utf-8"))["corridors"][0]["polyline"]
    terrain = json.loads(Path("data/terrain.json").read_text(encoding="utf-8"))
    try:
        scen = json.loads(Path("data/scenarios.json").read_text(encoding="utf-8"))["scenarios"]
        exp = next((s["expect"] for s in scen if s["id"] == "s1-0800"), {})
    except Exception:  # noqa: BLE001
        exp = {}
    return poly, terrain, exp


def fit_projector(poly, rect):
    """把经纬度包围盒等比例放进 rect，返回 (投影函数, 缩放 k)。"""
    x0, y0, x1, y1 = rect
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    w, h = max(1e-9, maxx - minx), max(1e-9, maxy - miny)
    k = min((x1 - x0) / w, (y1 - y0) / h)
    ox = x0 + ((x1 - x0) - w * k) / 2
    oy = y0 + ((y1 - y0) - h * k) / 2
    return (lambda p: (ox + (p[0] - minx) * k, oy + (maxy - p[1]) * k)), k


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="阳光充足")
    ap.add_argument("--subtitle", default="基于气象与能耗预测的物流调度")
    ap.add_argument("--claim", default="避雨 + 补能，一次算出一条能走的路")
    ap.add_argument("--outdir", default="banner")
    ap.add_argument("--stem", default="阳光充足-banner-1200x675")
    ap.add_argument("--bg", default=None, help="ComfyUI 生成的底纹（可选）")
    ap.add_argument("--bg-mix", type=float, default=0.80, help="底色叠加强度 0~1")
    args = ap.parse_args()

    HEI, HEIB = "msyh.ttc", "msyhbd.ttc"

    # ── 背景 ──
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    for i in range(H):
        t = i / H
        d.line([(0, i), (W, i)], fill=(int(247 - 8 * t), int(250 - 6 * t), int(253 - 3 * t)))
    if args.bg and Path(args.bg).exists():
        src = Image.open(args.bg).convert("RGB")
        sc = max(W / src.width, H / src.height)
        src = src.resize((round(src.width * sc), round(src.height * sc)), Image.LANCZOS)
        src = src.crop(((src.width - W) // 2, (src.height - H) // 2,
                        (src.width - W) // 2 + W, (src.height - H) // 2 + H))
        img = Image.blend(src, img, args.bg_mix)
        d = ImageDraw.Draw(img)

    # 网格
    for x in range(0, W, 40):
        d.line([(x, 0), (x, H)], fill=(236, 242, 248))
    for y in range(0, H, 40):
        d.line([(0, y), (W, y)], fill=(236, 242, 248))

    # ── 顶栏 ──
    d.rectangle([0, 0, W, 4], fill=SKY)
    d.rounded_rectangle([56, 46, 56 + 46, 46 + 46], radius=13, fill=SKY)
    fmark = font(HEIB, 26)
    d.text((56 + 23, 46 + 23), "晴", font=fmark, fill=(255, 255, 255), anchor="mm")

    ft = font(HEIB, 52)
    draw_spaced(d, (120, 44), args.title, ft, INK, gap=3)
    fs = font(HEI, 19)
    d.text((122, 104), args.subtitle, font=fs, fill=INK2, anchor="la")
    fbadge = font(HEI, 15)
    bt = "百度地图开发者创作大赛 · 智能物流"
    bw = d.textlength(bt, font=fbadge) + 26
    d.rounded_rectangle([W - 56 - bw, 56, W - 56, 88], radius=16, fill=SKY_L, outline=(190, 214, 234))
    d.text((W - 56 - bw / 2, 72), bt, font=fbadge, fill=SKY_D, anchor="mm")

    # ── 中部：地图面板（数据驱动绘制） ──
    px0, py0, px1, py1 = 56, 148, 760, 492
    d.rounded_rectangle([px0, py0, px1, py1], radius=14, fill=(255, 255, 255), outline=LINE)
    panel = img.crop((px0, py0, px1, py1))
    poly, terrain, exp = load_geo()
    proj, k = fit_projector(poly, (px0 + 42, py0 + 40, px1 - 42, py1 - 118))

    # 面板内网格
    for gx in range(px0 + 40, px1 - 20, 46):
        d.line([(gx, py0 + 6), (gx, py1 - 6)], fill=(242, 246, 250))
    for gy in range(py0 + 30, py1 - 10, 46):
        d.line([(px0 + 6, gy), (px1 - 6, gy)], fill=(242, 246, 250))

    pts = [proj(p) for p in poly]
    cum = [0.0]
    for i in range(1, len(poly)):
        dlat = poly[i][1] - poly[i - 1][1]
        dlng = (poly[i][0] - poly[i - 1][0]) * 0.84
        cum.append(cum[-1] + math.hypot(dlng, dlat) * 111.0)
    total = cum[-1]

    # 原路线
    d.line(pts, fill=(178, 190, 201), width=5, joint="curve")
    # 高风险段（红）
    band = (terrain.get("lowland_bands") or [{"km0": 430, "km1": 470}])[0]
    seg = [pts[i] for i in range(len(pts)) if band["km0"] - 12 <= cum[i] <= band["km1"] + 12]
    if len(seg) > 1:
        d.line(seg, fill=(246, 205, 201), width=20, joint="curve")
        d.line(seg, fill=DANGER, width=8, joint="curve")
    # 绕行（蓝，示意：横向推开）
    if len(seg) > 1:
        off = []
        for i in range(len(pts)):
            amp = 0.0
            if band["km0"] - 22 <= cum[i] <= band["km1"] + 22:
                a = min(1.0, (cum[i] - (band["km0"] - 22)) / 16)
                b = min(1.0, ((band["km1"] + 22) - cum[i]) / 16)
                amp = min(a, b)
                amp = amp * amp * (3 - 2 * amp)
            if amp <= 0:
                off.append(None)
            else:
                j = min(len(pts) - 1, i + 3)
                kk = max(0, i - 3)
                ang = math.atan2(pts[j][1] - pts[kk][1], pts[j][0] - pts[kk][0]) + math.pi / 2
                off.append((pts[i][0] + math.cos(ang) * 15 * amp,
                            pts[i][1] + math.sin(ang) * 15 * amp))
        run = []
        for i, p in enumerate(off):
            if p and seg and cum[i] >= band["km0"] - 22 and cum[i] <= band["km1"] + 22:
                if not run:
                    run.append(pts[i])
                run.append(p)
            elif run:
                run.append(pts[i])
                d.line(run, fill=SKY, width=5, joint="curve")
                run = []
        if run:
            d.line(run, fill=SKY, width=5, joint="curve")

    # 补给站
    sts = json.loads(Path("data/stations.json").read_text(encoding="utf-8"))["stations"]
    for st in sts:
        best, bi = 1e9, 0
        for i in range(len(cum)):
            if abs(cum[i] - st["km"]) < best:
                best, bi = abs(cum[i] - st["km"]), i
        if abs(cum[bi] - st["km"]) > 16:
            continue
        x, y = pts[bi]
        inside = band["km0"] <= st["km"] <= band["km1"]
        d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(255, 255, 255))
        d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=DANGER if inside else GO)

    # 端点
    for p, name, anc in ((pts[0], "西安", "rd"), (pts[-1], "成都", "la")):
        d.ellipse([p[0] - 5, p[1] - 5, p[0] + 5, p[1] + 5], fill=INK)
        d.text(p, name, font=font(HEIB, 15), fill=INK, anchor=anc)

    # 面板角标
    d.text((px0 + 16, py0 + 12), "G5 京昆高速 · 690 km", font=font(HEI, 13), fill=INK3, anchor="la")
    d.text((px1 - 16, py1 - 14), "示意 · 数据驱动绘制", font=font(HEI, 12), fill=INK3, anchor="rd")

    # 面板内图例（放在干线不走的下方）
    lx, ly = px0 + 24, py1 - 74
    for i, (col, wid, label) in enumerate((( (178, 190, 201), 5, "原路线"),
                                          (SKY, 5, "优化路线（并行国省道）"),
                                          (DANGER, 6, "高风险积水段"))):
        yy = ly + i * 20
        d.line([(lx, yy), (lx + 18, yy)], fill=col, width=wid)
        d.text((lx + 25, yy), label, font=font(HEI, 13), fill=INK2, anchor="lm")
    d.ellipse([lx - 3, ly + 52, lx + 9, ly + 64], fill=GO)
    d.text((lx + 25, ly + 58), "可用补给站", font=font(HEI, 13), fill=INK2, anchor="lm")

    # ── 右侧：KPI ──
    kx, ky = 800, 158
    kpis = [
        ("690 km", "西安 → 成都 全程", INK),
        (f"{exp.get('hazard_peak_rain', 46.1)} mm/h", "沿途降水峰值", DANGER),
        ("−93%", "积水风险暴露", GO),
    ]
    for i, (big, small, col) in enumerate(kpis):
        y = ky + i * 96
        d.rounded_rectangle([kx, y, W - 56, y + 82], radius=12, fill=(255, 255, 255), outline=LINE)
        d.rectangle([kx, y + 16, kx + 4, y + 66], fill=col)
        d.text((kx + 20, y + 40), big, font=font(HEIB, 32), fill=col, anchor="lm")
        d.text((kx + 20, y + 64), small, font=font(HEI, 14), fill=INK2, anchor="lm")

    # ── 主张 ──
    fy = 512
    d.text((56, fy), args.claim, font=font(HEIB, 30), fill=INK, anchor="la")
    d.text((58, fy + 42), "沿路线逐小时气象 → 积水风险场 → 坡度/风阻/暴雨加权能耗 → 补给站与绕行统一择优",
           font=font(HEI, 15), fill=INK2, anchor="la")

    # ── 页脚 ──
    d.line([(56, 596), (W - 56, 596)], fill=LINE, width=1)
    d.text((56, 610), "百度地图 API：JSAPI GL（WebGL 渲染）· 天气查询（经纬度逐小时）· 驾车路线规划 v2 · 地点检索",
           font=font(HEI, 13), fill=INK3, anchor="la")
    d.text((W - 56, 610), "气象与能耗为模型预测，非实测；仅供调度参考", font=font(HEI, 13), fill=DANGER, anchor="ra")

    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    png, jpg = out / f"{args.stem}.png", out / f"{args.stem}.jpg"
    img.save(png, "PNG", optimize=True)
    img.convert("RGB").save(jpg, "JPEG", quality=92, optimize=True)
    for p in (png, jpg):
        print(f"{p}  {p.stat().st_size / 1024:.0f} KB")
    print(f"尺寸 {img.size[0]}x{img.size[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
