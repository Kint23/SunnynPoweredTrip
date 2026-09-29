# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""合成一段**原创轻快轻音乐**（无版权顾虑），供正片使用。

风格：西式轻音乐 / easy listening。G 大调 I–V–vi–IV（G–D–Em–C），112 BPM，
马林巴质感的亮拨弦 + 柔和衬底 + 沙锤半拍 + 轻木击，尾部多抽头扩散当混响。

    uv run tools/make_music.py --seconds 62 --out video/music.wav
"""
from __future__ import annotations

import argparse
import math
import sys
import wave
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(errors="replace")

SR = 44100
BPM = 112.0
BEAT = 60.0 / BPM
EIGHTH = BEAT / 2.0

# G 大调 I–V–vi–IV：(三和弦三个音, 低音)
PROG = [
    ((392.00, 493.88, 587.33), 196.00),  # G   G B D
    ((293.66, 369.99, 440.00), 146.83),  # D   D F# A
    ((329.63, 392.00, 493.88), 164.81),  # Em  E G B
    ((261.63, 329.63, 392.00), 130.81),  # C   C E G
]

# 每小节 8 个八分音的琶音型（0/1/2=和弦音，3=根音高八度）
PATTERNS = [
    [0, 1, 2, 3, 2, 1, 0, 2],
    [0, 2, 1, 3, 1, 2, 0, 1],
    [3, 1, 2, 0, 2, 1, 3, 2],
    [0, 1, 3, 2, 1, 0, 2, 1],
]


def pluck(freq: float, dur: float, amp: float = 1.0) -> np.ndarray:
    """亮而短的拨弦（马林巴/钢片琴质感）。"""
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float64) / SR
    out = np.zeros(n)
    for k in range(1, 10):
        a = amp * (0.50 ** (k - 1)) / k
        if a < 2e-4:
            break
        tau = 0.60 / (1.0 + 0.35 * (k - 1))
        out += a * np.sin(2 * np.pi * freq * k * t + 0.5 * k) * np.exp(-t / tau)
    return out * (1.0 - np.exp(-t / 0.0025))


def pad(freqs, dur: float, amp: float) -> np.ndarray:
    """柔和衬底：慢起慢落，轻微颤动。"""
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float64) / SR
    out = np.zeros(n)
    for f in freqs:
        out += np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * f * 2 * t)
    out *= amp * (1.0 + 0.05 * np.sin(2 * np.pi * 0.19 * t))
    attack = np.minimum(1.0, t / 0.35)
    release = np.minimum(1.0, (dur - t) / 0.45)
    return out * attack * np.clip(release, 0, 1)


def shaker(amp: float = 0.30) -> np.ndarray:
    """沙锤：高通噪声 + 极快衰减。"""
    n = int(0.05 * SR)
    rng = np.random.default_rng(23)
    noise = rng.normal(0, 1, n)
    hp = np.diff(noise, prepend=0.0)  # 一阶高通
    t = np.arange(n, dtype=np.float64) / SR
    return amp * hp * np.exp(-t / 0.008)


def tick(freq: float = 1180.0, amp: float = 0.22) -> np.ndarray:
    n = int(0.06 * SR)
    t = np.arange(n, dtype=np.float64) / SR
    return amp * np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.012)


def bass(freq: float, dur: float, amp: float = 0.5) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float64) / SR
    out = (np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * freq * 2 * t)
           + 0.12 * np.sin(2 * np.pi * freq * 3 * t))
    return amp * out * np.exp(-t / 0.85) * (1.0 - np.exp(-t / 0.006))


def add(buf: np.ndarray, sig: np.ndarray, at: int, gain: float = 1.0) -> None:
    if at >= len(buf):
        return
    n = min(len(sig), len(buf) - at)
    if n > 0:
        buf[at:at + n] += sig[:n] * gain


def diffuse(x: np.ndarray, seed: int, taps: int = 10):
    """多抽头扩散 + 左右不同延迟 -> 廉价但有空间感的混响与立体声宽度。"""
    rng = np.random.default_rng(seed)
    left = np.zeros_like(x)
    right = np.zeros_like(x)
    for _ in range(taps):
        d = float(rng.uniform(0.010, 0.22))
        g = float(rng.uniform(0.10, 0.34)) * math.exp(-d * 4.0)
        n = int(d * SR)
        if n < len(x):
            left[n:] += x[:-n] * g
            n2 = max(1, int(d * float(rng.uniform(0.92, 1.08)) * SR))
            if n2 < len(x):
                right[n2:] += x[:-n2] * g
    return left, right


def build(seconds: float) -> np.ndarray:
    total = int(seconds * SR)
    arp = np.zeros(total)
    pads = np.zeros(total)
    perc = np.zeros(total)
    lows = np.zeros(total)
    rng = np.random.default_rng(31415)

    bar_len = BEAT * 4
    n_bars = int(seconds / bar_len) + 1
    for bar in range(n_bars):
        t_bar = bar * bar_len
        at_bar = int(t_bar * SR)
        triad, root = PROG[bar % 4]
        pattern = PATTERNS[(bar + (bar // 4)) % 4]

        # 旋律琶音
        for step, pi in enumerate(pattern):
            at = int((t_bar + step * EIGHTH) * SR)
            note = triad[pi % 3] * (2.0 if pi == 3 else 1.0)
            if rng.random() < 0.10:  # 偶尔加个上邻音，避免太机械
                note *= 1.1225
            accent = 1.0 if step in (0, 4) else (0.72 if step % 2 == 0 else 0.55)
            dur = 1.1 if step == 0 else 0.75
            add(arp, pluck(note, dur, amp=0.42), at, accent)

        # 衬底 + 低音
        add(pads, pad(triad, bar_len, 0.055), at_bar)
        add(lows, bass(root, bar_len * 0.9, 0.34), at_bar)
        add(lows, bass(root, bar_len * 0.45, 0.20), int((t_bar + 2 * BEAT) * SR))

        # 沙锤半拍 + 2/4 拍轻击
        for step in (1, 3, 5, 7):
            add(perc, shaker(0.24), int((t_bar + step * EIGHTH) * SR))
        for b in (1, 3):
            add(perc, tick(amp=0.16), int((t_bar + b * BEAT) * SR))

    dry = arp * 1.0 + pads + perc + lows
    left, right = diffuse(dry, 90210)
    left = dry * 0.70 + left * 0.50
    right = dry * 0.70 + right * 0.50

    stereo = np.stack([left, right], axis=1)
    fin = int(0.9 * SR)
    fout = int(2.5 * SR)
    stereo[:fin] *= np.linspace(0, 1, fin)[:, None]
    stereo[-fout:] *= np.linspace(1, 0, fout)[:, None]
    peak = float(np.max(np.abs(stereo)))
    if peak > 0:
        stereo = stereo / peak * 0.90
    return stereo


def write_wav(path: Path, stereo: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ints = (np.clip(stereo, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(ints.tobytes())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=62.0)
    ap.add_argument("--out", default="video/music.wav")
    args = ap.parse_args()

    print(f"合成 {args.seconds:.0f}s 原创轻快轻音乐（{BPM:.0f} BPM，G 大调 I-V-vi-IV）...",
          flush=True)
    stereo = build(args.seconds)
    p = Path(args.out)
    write_wav(p, stereo)
    print(f"{p}  {p.stat().st_size / 1024 / 1024:.1f} MB  {len(stereo) / SR:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
