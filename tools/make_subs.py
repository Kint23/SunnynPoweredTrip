# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""把 SRT 转成**显式声明 PlayRes 的 ASS**，供 ffmpeg 烧录。

为什么要这一步：libass 读 SRT 时会套用 ASS 的默认 `PlayResY=288`，
于是 `force_style` 里的 FontSize 会被放大 1080/288 ≈ 3.75 倍（字大得离谱）。
自己写 ASS 把 `PlayResX/PlayResY` 直接声明成成片分辨率，字号就是**像素**。

    uv run tools/make_subs.py --srt video/正片字幕.srt --out video/subs.ass
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(errors="replace")

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},&H00FFFFFF,&H000000FF,&H78141008,&H00000000,0,0,0,0,100,100,2,0,3,{pad},0,2,80,80,{mv},134

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

TAG = re.compile(r"<[^>]+>")


def srt_time_to_ass(t: str) -> str:
    m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", t.strip())
    if not m:
        raise ValueError(f"时间格式不对: {t}")
    h, mi, s, ms = (int(x) for x in m.groups())
    cs = ms // 10  # 百分秒
    return f"{h:d}:{mi:02d}:{s:02d}.{cs:02d}"


def parse_srt(text: str) -> list[tuple[str, str, str]]:
    blocks = re.split(r"\r?\n\r?\n+", text.strip())
    out = []
    for b in blocks:
        lines = [ln for ln in b.splitlines() if ln.strip() != ""]
        if len(lines) < 2:
            continue
        # 第一条可能是序号
        idx = 0
        if lines[0].strip().isdigit():
            idx = 1
        if idx >= len(lines) or "-->" not in lines[idx]:
            continue
        a, b2 = [x.strip() for x in lines[idx].split("-->")[:2]]
        body = "\\N".join(TAG.sub("", ln).strip() for ln in lines[idx + 1:])
        out.append((srt_time_to_ass(a), srt_time_to_ass(b2), body))
    return out


def ass_time(t: float) -> str:
    """秒 -> ASS 的 h:mm:ss.cc（百分秒）。"""
    total = int(round(t * 100))
    cs = total % 100
    total //= 100
    s = total % 60
    m = total // 60 % 60
    h = total // 3600
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def write_ass(subs, out, *, width: int = 1920, height: int = 1080,
              font: str = "KaiTi", size: int = 46, pad: float = 8.0,
              margin_v: int = 66) -> Path:
    """subs = [(start_sec, end_sec, text), ...] -> 带正确 PlayRes 的 ASS 文件。"""
    head = HEADER.format(w=width, h=height, font=font, size=size,
                         pad=pad, mv=margin_v)
    lines = [f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Default,,0,0,0,,{t}"
             for a, b, t in subs]
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--srt", default="video/正片字幕.srt")
    ap.add_argument("--out", default="video/subs.ass")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--font", default="KaiTi")
    ap.add_argument("--size", type=int, default=46)
    ap.add_argument("--pad", type=float, default=8.0, help="BorderStyle=3 时的文字底框内边距")
    ap.add_argument("--margin-v", type=int, default=66, dest="mv")
    args = ap.parse_args()

    events = parse_srt(Path(args.srt).read_text(encoding="utf-8"))
    if not events:
        raise SystemExit(f"没解析出字幕：{args.srt}")

    head = HEADER.format(w=args.width, h=args.height, font=args.font,
                         size=args.size, pad=args.pad, mv=args.mv)
    lines = [f"Dialogue: 0,{a},{b},Default,,0,0,0,,{t}" for a, b, t in events]
    p = Path(args.out)
    p.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"{p}  {len(events)} 条  FontSize={args.size}px @ {args.width}x{args.height}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
