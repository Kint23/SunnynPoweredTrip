# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Generate images via the local ComfyUI HTTP API (Qwen-Image 2512 + Lightning 4-step LoRA).

Standard library only - no third-party deps needed.

  uv run tools/comfy_generate.py --prompt-file tools/prompt_banner.txt --out banner/bg.png

The graph mirrors ComfyUI's official template
`image_qwen_image_2512_with_2steps_lora.json` (core nodes only):
  UNETLoader -> LoraLoaderModelOnly -> ModelSamplingAuraFlow -> KSampler
  CLIPLoader -> CLIPTextEncode (+ConditioningZeroOut as negative)
  EmptySD3LatentImage -> KSampler -> VAEDecode -> SaveImage
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(errors="replace")

DEFAULT_SERVER = "http://127.0.0.1:8188"
UNET = "qwen_image_2512_fp8_e4m3fn.safetensors"
CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"
LORA = "Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors"

SAVE_NODE = "123"


def build_graph(prompt, width, height, steps, cfg, shift, seed, lora, strength, prefix, batch):
    """Return an API-format prompt graph."""
    return {
        "103": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "104": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"},
        },
        "105": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": UNET, "weight_dtype": "default"},
        },
        "114": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["105", 0],
                "lora_name": lora,
                "strength_model": strength,
            },
        },
        "110": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"model": ["114", 0], "shift": shift},
        },
        "108": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["104", 0], "text": prompt},
        },
        "128": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["108", 0]},
        },
        "107": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": batch},
        },
        "106": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["110", 0],
                "positive": ["108", 0],
                "negative": ["128", 0],
                "latent_image": ["107", 0],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
            },
        },
        "109": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["106", 0], "vae": ["103", 0]},
        },
        SAVE_NODE: {
            "class_type": "SaveImage",
            "inputs": {"images": ["109", 0], "filename_prefix": prefix},
        },
    }


def _get_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post_json(url, payload, timeout=60):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def download_image(server, img, dest):
    q = urllib.parse.urlencode(
        {
            "filename": img["filename"],
            "subfolder": img.get("subfolder", ""),
            "type": img.get("type", "output"),
        }
    )
    with urllib.request.urlopen(f"{server}/view?{q}", timeout=120) as r:
        blob = r.read()
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(blob)
    return len(blob)


def generate(
    prompt,
    out,
    *,
    server=DEFAULT_SERVER,
    width=1664,
    height=928,
    steps=4,
    cfg=1.0,
    shift=3.0,
    seed=None,
    lora=LORA,
    strength=1.0,
    prefix="djjwl-banner",
    batch=1,
    timeout=1800,
    quiet=False,
):
    """Queue one job, wait for it, download the first image to `out`."""
    if width % 16 or height % 16:
        raise SystemExit(f"width/height must be multiples of 16 (got {width}x{height})")
    if seed is None:
        seed = int.from_bytes(os.urandom(8), "little") % (2**53)

    def say(*a):
        if not quiet:
            print(*a, flush=True)

    # Fail fast with a clear message if the server is not up.
    try:
        stats = _get_json(f"{server}/system_stats", timeout=8)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Cannot reach ComfyUI at {server} ({exc}). Is it running?") from exc
    say(f"ComfyUI {stats['system']['comfyui_version']}  gpu={stats['devices'][0]['name']}")
    say(f"size={width}x{height}  steps={steps}  cfg={cfg}  seed={seed}  lora={lora}")

    graph = build_graph(prompt, width, height, steps, cfg, shift, seed, lora, strength, prefix, batch)
    try:
        res = _post_json(f"{server}/prompt", {"prompt": graph, "client_id": "codex-djjwl"})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"ComfyUI rejected the graph (HTTP {exc.code}):\n{body}") from exc

    prompt_id = res["prompt_id"]
    if res.get("node_errors"):
        raise SystemExit(f"node_errors: {json.dumps(res['node_errors'], ensure_ascii=False)}")
    say(f"queued prompt_id={prompt_id}")

    t0 = time.time()
    history = None
    while time.time() - t0 < timeout:
        try:
            hist = _get_json(f"{server}/history/{prompt_id}", timeout=20)
        except Exception:  # noqa: BLE001
            hist = {}
        if prompt_id in hist:
            history = hist[prompt_id]
            break
        time.sleep(2)
    if history is None:
        raise SystemExit(f"timed out after {timeout}s waiting for {prompt_id}")

    status = history.get("status", {})
    if status.get("status_str") == "error" or not status.get("completed", True):
        msgs = json.dumps(status.get("messages", []), ensure_ascii=False, indent=2)
        raise SystemExit(f"generation failed:\n{msgs}")

    imgs = history.get("outputs", {}).get(SAVE_NODE, {}).get("images", [])
    if not imgs:
        raise SystemExit(f"no images in outputs: {json.dumps(history.get('outputs', {}))}")

    saved = []
    for i, img in enumerate(imgs):
        dest = out if len(imgs) == 1 else _suffixed(out, i + 1)
        n = download_image(server, img, dest)
        saved.append(dest)
        say(f"saved {dest}  {n / 1024:.0f} KB")
    say(f"done in {time.time() - t0:.1f}s")
    return saved


def _suffixed(path, n):
    root, ext = os.path.splitext(path)
    return f"{root}-{n}{ext}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate an image through the local ComfyUI API")
    ap.add_argument("--prompt", default=None, help="text prompt")
    ap.add_argument("--prompt-file", default=None, help="read prompt from a UTF-8 file")
    ap.add_argument("--out", required=True, help="output image path (png/jpg)")
    ap.add_argument("--server", default=DEFAULT_SERVER)
    ap.add_argument("--width", type=int, default=1664)
    ap.add_argument("--height", type=int, default=928)
    ap.add_argument("--steps", type=int, default=4)
    ap.add_argument("--cfg", type=float, default=1.0)
    ap.add_argument("--shift", type=float, default=3.0)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--lora", default=LORA)
    ap.add_argument("--lora-strength", type=float, default=1.0)
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--prefix", default="djjwl-banner")
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args(argv)

    prompt = args.prompt
    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as fh:
            prompt = fh.read().strip()
    if not prompt:
        raise SystemExit("need --prompt or --prompt-file")

    generate(
        prompt,
        args.out,
        server=args.server,
        width=args.width,
        height=args.height,
        steps=args.steps,
        cfg=args.cfg,
        shift=args.shift,
        seed=args.seed,
        lora=args.lora,
        strength=args.lora_strength,
        batch=args.batch,
        prefix=args.prefix,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
