# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""校验 Banner 是否符合提交规格：1200x675、PNG/JPG、<=5MB。

不依赖 Pillow —— 直接从文件头读宽高（PNG IHDR / JPEG SOFn / WebP VP8X|VP8|VP8L），
这样在没有第三方库的环境里也能跑，且不受 ffprobe 对某些 PNG 返回 0x0 的影响。

用法：
    uv run tools/check_banner.py            # 默认检查 banner/
    uv run tools/check_banner.py path/to/dir
退出码 0 = 官方 Banner 全部合规。
"""

from __future__ import annotations

import os
import struct
import sys

SPEC_W, SPEC_H = 1200, 675
SPEC_MAX_BYTES = 5 * 1024 * 1024
SPEC_EXTS = {".png", ".jpg", ".jpeg"}

# 辅助素材：README 动图等，不按官方 Banner 规格卡，只报出来供人确认。
AUX_HINT = ("teaser",)


def _png_size(buf: bytes) -> tuple[int, int] | None:
    if buf[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    # 第一个 chunk 必须是 IHDR：len(4) type(4) w(4) h(4)
    if buf[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", buf[16:24])
    return int(w), int(h)


def _jpeg_size(buf: bytes) -> tuple[int, int] | None:
    if buf[:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(buf)
    while i + 3 < n:
        if buf[i] != 0xFF:
            i += 1
            continue
        marker = buf[i + 1]
        i += 2
        # 填充与独立标记
        if marker in (0xFF, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if marker in (0xD8, 0xD9):
            continue
        if i + 1 >= n:
            return None
        seg_len = struct.unpack(">H", buf[i : i + 2])[0]
        # SOF0..SOF15，排除 DHT(C4)/JPG(C8)/DAC(CC)
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", buf[i + 3 : i + 7])
            return int(w), int(h)
        if marker == 0xDA:  # SOS，之后是熵编码数据，没有尺寸信息了
            return None
        i += seg_len
    return None


def _webp_size(buf: bytes) -> tuple[int, int] | None:
    if buf[:4] != b"RIFF" or buf[8:12] != b"WEBP":
        return None
    fourcc = buf[12:16]
    if fourcc == b"VP8X":
        w = int.from_bytes(buf[24:27], "little") + 1
        h = int.from_bytes(buf[27:30], "little") + 1
        return w, h
    if fourcc == b"VP8L":
        bits = int.from_bytes(buf[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if fourcc == b"VP8 ":
        if buf[23:26] != b"\x9d\x01\x2a":
            return None
        w = int.from_bytes(buf[26:28], "little") & 0x3FFF
        h = int.from_bytes(buf[28:30], "little") & 0x3FFF
        return w, h
    return None


def read_size(path: str) -> tuple[int, int] | None:
    with open(path, "rb") as fh:
        head = fh.read(4096)
    for probe in (_png_size, _jpeg_size, _webp_size):
        got = probe(head)
        if got:
            return got
    return None


def main(argv: list[str]) -> int:
    root = argv[1] if len(argv) > 1 else "banner"
    if not os.path.isdir(root):
        print(f"目录不存在：{root}")
        return 2

    official = []
    auxiliary = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        (auxiliary if any(k in name.lower() for k in AUX_HINT) else official).append(path)

    print(f"规格：{SPEC_W}x{SPEC_H} ｜ PNG/JPG ｜ <= {SPEC_MAX_BYTES // 1024 // 1024} MB")
    print(f"目录：{root}\n")

    bad = 0
    for path in official:
        size = os.path.getsize(path)
        dim = read_size(path)
        ext_ok = os.path.splitext(path)[1].lower() in SPEC_EXTS
        if dim is None:
            print(f"  无法识别  {os.path.basename(path)}")
            bad += 1
            continue
        w, h = dim
        checks = [
            (f"{w}x{h}", w == SPEC_W and h == SPEC_H, f"{SPEC_W}x{SPEC_H}"),
            (os.path.splitext(path)[1].lstrip(".").upper(), ext_ok, "PNG/JPG"),
            (f"{size / 1024:.1f} KB", size <= SPEC_MAX_BYTES, "<=5MB"),
        ]
        ok = all(c[1] for c in checks)
        if not ok:
            bad += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {os.path.basename(path)}")
        for shown, good, want in checks:
            print(f"        {'v' if good else 'x'} {shown:<12} 应为 {want}")
        print(f"        比例 {w / h:.4f}（16:9 = 1.7778）")

    for path in auxiliary:
        size = os.path.getsize(path)
        dim = read_size(path)
        dim_s = f"{dim[0]}x{dim[1]}" if dim else "无法识别"
        print(f"  AUX   {os.path.basename(path)}  {dim_s}  {size / 1024:.1f} KB")
        print("        （README 动图，不受 1200x675 约束，只需体积可接受）")

    print()
    if bad:
        print(f"{bad} 个官方 Banner 不合规")
        return 1
    print(f"官方 Banner {len(official)} 个全部合规")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
