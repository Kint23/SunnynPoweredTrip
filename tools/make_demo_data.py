# /// script
# requires-python = ">=3.10"
# ///
"""离线生成演示数据（**确定性、可复现、不联网、不消耗配额**）。

为什么要有这一步（本项目最重要的工程纪律）：
  地点检索未认证仅 100 次/日，天气/算路也都有配额；而 Demo 与演示视频必须**可复现**。
  所以前端**只读 `data/*.json`**，线上调用全部由"离线预抓"这一步落到磁盘。
  本脚本是"预抓"的**确定性替身**：在没有服务端 AK 的情况下，用物理上合理的模型
  合成一份"仿真缓存"，让整条链路（气象 → 风险 → 能耗 → 补给 → 重规划）今天就能跑通。

  真实数据接入时，只需把 `tools/fetch_weather.py` / `fetch_stations.py` 的产物
  按同样的字段写进同名文件即可，前端与算法一行都不用改。

用法：
  uv run tools/make_demo_data.py
  uv run tools/make_demo_data.py --outdir data
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

# ─────────────────────────────────────────────────────────────
# 1. 干线：西安 → 成都（G5 京昆高速）
#    (名称, WGS-84 经度, WGS-84 纬度, 海拔 m)
#    海拔是真实量级（秦岭分水岭 ~1400m），用于"地形加权能耗"。
# ─────────────────────────────────────────────────────────────
ANCHORS: list[tuple[str, float, float, float]] = [
    ("西安·灞桥", 109.0700, 34.3300, 400),
    ("蓝田服务区", 109.3200, 34.1500, 620),
    ("秦岭终南山隧道", 108.9500, 33.9000, 1400),
    ("柞水服务区", 108.9300, 33.6400, 980),
    ("宁陕服务区", 108.3200, 33.3200, 780),
    ("洋县服务区", 107.5500, 33.2200, 560),
    ("汉中服务区", 107.0300, 33.0700, 510),
    ("勉县服务区", 106.6700, 33.1500, 560),
    ("宁强服务区", 106.2600, 32.8300, 830),
    ("朝天服务区", 105.9000, 32.6400, 700),
    ("广元服务区", 105.8400, 32.4400, 500),
    ("剑门关服务区", 105.5200, 32.2500, 720),
    ("江油服务区", 104.7500, 31.7800, 530),
    ("绵阳服务区", 104.6800, 31.4700, 470),
    ("德阳服务区", 104.3900, 31.1300, 480),
    ("成都·青白江", 104.2500, 30.8800, 490),
]

# 低洼易积水带（按干线里程）：广元—剑门关是嘉陵江河谷，历史上内涝频发；
# 江油—绵阳为涪江沿岸，地势略高，权重低一档。
# 注意：低洼是“闸门”——雨大不等于积水，必须落在低洼段才会形成高风险。
LOWLAND: list[tuple[float, float, float]] = [
    (430.0, 470.0, 0.95),   # 广元城区河谷段（40 km 的短低洼带——绕行才有意义）
    (560.0, 640.0, 0.50),   # 江油 — 绵阳（涪江沿岸，权重低一档）
]
LOWLAND_FALLOFF_KM = 5.0    # 横向离开干线 5 km 后，低洼权重衰减到 0

# 演示用"雨带"：沿里程的高斯带 × 时间上的高斯带（原地发展、夜间消散）
BAND = {"center_km": 450.0, "sigma_km": 35.0, "center_h": 13.5,
        "sigma_h": 1.8, "peak_mm_h": 55.0, "bg_mm_h": 0.4}

SAMPLE_STEP_KM = 25.0     # 沿程采样间隔（真实产品里也是这个量级，直接决定配额消耗）
PER_SEG = 14              # Catmull-Rom 每段加密点数
HOURS = 24

# ─────────────────────────────────────────────────────────────
# 2. 坐标换算：WGS-84 → GCJ-02 → BD-09
#    百度地图 JSAPI 用 BD-09；国际数据源用 WGS-84。必须换算，否则整体偏移 300~600m。
# ─────────────────────────────────────────────────────────────
_A = 6378245.0
_EE = 0.00669342162296594323
_X_PI = math.pi * 3000.0 / 180.0


def _out_of_china(lng: float, lat: float) -> bool:
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(lng: float, lat: float) -> float:
    ret = (-100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat
           + 0.2 * math.sqrt(abs(lng)))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * math.pi) + 320 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(lng: float, lat: float) -> float:
    ret = (300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat
           + 0.1 * math.sqrt(abs(lng)))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * math.pi) + 40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * math.pi) + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    if _out_of_china(lng, lat):
        return lng, lat
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return lng + dlng, lat + dlat


def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    """数值反解（三轮不动点），用于往返自检。"""
    wlng, wlat = lng, lat
    for _ in range(3):
        glng, glat = wgs84_to_gcj02(wlng, wlat)
        wlng += lng - glng
        wlat += lat - glat
    return wlng, wlat


def _bd_factor(lng: float, lat: float) -> tuple[float, float]:
    x = math.pi * 3000.0 / 180.0
    z = math.atan2(math.sqrt(3.0) * math.cos(lat * math.pi / 180.0) * math.sin(lng * x * 0.5)
                   - 0.5, math.cos(lat * math.pi / 180.0) * math.cos(lng * x * 0.5))
    return lng + (180.0 - 180.0 * z / math.pi), lat


def gcj02_to_bd09(lng: float, lat: float) -> tuple[float, float]:
    x, y = lng, lat
    z = math.sqrt(x * x + y * y) + 0.00002 * math.sin(y * _X_PI)
    theta = math.atan2(y, x) + 0.000003 * math.cos(x * _X_PI)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006


def bd09_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    x, y = lng - 0.0065, lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * _X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * _X_PI)
    return z * math.cos(theta), z * math.sin(theta)


def wgs84_to_bd09(lng: float, lat: float) -> tuple[float, float]:
    return gcj02_to_bd09(*wgs84_to_gcj02(lng, lat))


# ─────────────────────────────────────────────────────────────
# 3. 几何：Catmull-Rom 加密 → 沿里程采样
# ─────────────────────────────────────────────────────────────
def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371.0088
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    dp = p2 - p1
    dl = math.radians(b[0] - a[0])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def catmull_rom(pts: list[tuple[float, float]], per_seg: int = 12) -> list[tuple[float, float]]:
    """把控制点拟合成平滑曲线——高速干线是曲线，不是折线。"""
    if len(pts) < 2:
        return list(pts)
    p = [pts[0]] + list(pts) + [pts[-1]]
    out: list[tuple[float, float]] = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for s in range(per_seg):
            t = s / per_seg
            t2, t3 = t * t, t * t * t
            f0 = -0.5 * t3 + t2 - 0.5 * t
            f1 = 1.5 * t3 - 2.5 * t2 + 1.0
            f2 = -1.5 * t3 + 2.0 * t2 + 0.5 * t
            f3 = 0.5 * t3 - 0.5 * t2
            out.append((p0[0] * f0 + p1[0] * f1 + p2[0] * f2 + p3[0] * f3,
                        p0[1] * f0 + p1[1] * f1 + p2[1] * f2 + p3[1] * f3))
    out.append(pts[-1])
    return out


def cumulative_km(poly: list[tuple[float, float]]) -> list[float]:
    cum = [0.0]
    for i in range(1, len(poly)):
        cum.append(cum[-1] + haversine_km(poly[i - 1], poly[i]))
    return cum


def resample_along(poly: list[tuple[float, float]], cum: list[float],
                   step: float) -> list[dict]:
    """按固定里程间隔取点（气象/能耗都在这些点上求解）。"""
    total = cum[-1]
    targets, d = [], 0.0
    while d < total:
        targets.append(d)
        d += step
    targets.append(total)

    out, j = [], 0
    for d in targets:
        while j < len(cum) - 2 and cum[j + 1] < d:
            j += 1
        seg = max(1e-9, cum[j + 1] - cum[j])
        t = min(1.0, max(0.0, (d - cum[j]) / seg))
        lng = poly[j][0] + (poly[j + 1][0] - poly[j][0]) * t
        lat = poly[j][1] + (poly[j + 1][1] - poly[j][1]) * t
        out.append({"km": round(d, 3), "wgs": (lng, lat)})
    return out


def lowland_weight(km: float) -> float:
    w = 0.15
    for a, b, v in LOWLAND:
        if a <= km <= b:
            w = max(w, v)
    return round(w, 2)


# ─────────────────────────────────────────────────────────────
# 4. 海拔剖面（分段线性；换算成坡度用于能耗模型）
# ─────────────────────────────────────────────────────────────
def elevation_at(km: float, anchor_km: list[tuple[float, float]]) -> float:
    if km <= anchor_km[0][0]:
        return anchor_km[0][1]
    for i in range(1, len(anchor_km)):
        k0, e0 = anchor_km[i - 1]
        k1, e1 = anchor_km[i]
        if km <= k1:
            t = (km - k0) / max(1e-9, k1 - k0)
            return e0 + (e1 - e0) * t
    return anchor_km[-1][1]


# ─────────────────────────────────────────────────────────────
# 5. 气象：雨带（空间高斯 × 时间高斯）+ 日变化
# ─────────────────────────────────────────────────────────────
def weather_at(km: float, hour: int) -> dict:
    gs = math.exp(-0.5 * ((km - BAND["center_km"]) / BAND["sigma_km"]) ** 2)
    gt = math.exp(-0.5 * ((hour - BAND["center_h"]) / BAND["sigma_h"]) ** 2)
    # 沿程散布的小阵雨，让"背景"不是零——否则图上一眼假
    speck = 0.3 * (1 + math.sin(km / 37.0 + hour * 0.9)) * 0.5
    rain = BAND["bg_mm_h"] + speck + BAND["peak_mm_h"] * gs * gt

    wind = 3.4 + 0.11 * rain + 1.1 * math.sin(hour / 5.3 + km / 220.0)
    wind_dir = (200.0 + 55.0 * math.sin(km / 160.0) + 12.0 * hour) % 360.0
    vis = max(0.6, 22.0 - 0.42 * rain - 0.25 * max(0.0, rain - 12.0))
    temp = 23.0 + 8.5 * math.sin((hour - 9.0) / 24.0 * 2 * math.pi)
    return {
        "h": hour,
        "rain_mm_h": round(rain, 2),
        "wind_ms": round(max(0.0, wind), 2),
        "wind_dir_deg": round(wind_dir, 1),
        "vis_km": round(vis, 2),
        "temp_c": round(temp, 1),
    }


# ─────────────────────────────────────────────────────────────
# 6. 补给站（金标：名称与位置为依公开资料整理的**示意数据**，需用地点检索核验）
# ─────────────────────────────────────────────────────────────
STATIONS: list[dict] = [
    # (名称, 桩号 km, 类型, 功率 kW, 电价 元/kWh, 绕行 km, 电话, 备注)
    ("蓝田服务区·充电站", 42, "charge", 240, 1.62, 0.0, "029-8800xxxx", "枪位 8"),
    ("柞水服务区·充电站", 118, "charge", 240, 1.68, 0.0, "0914-7700xxx", "枪位 6"),
    ("宁陕服务区·充电站", 168, "charge", 180, 1.72, 0.0, "0915-6600xxx", "枪位 4 · 排队风险"),
    ("洋县服务区·充电站", 236, "charge", 240, 1.58, 0.0, "0916-8200xxx", "枪位 8"),
    ("汉中服务区·充电站", 288, "charge", 300, 1.55, 0.0, "0916-2500xxx", "枪位 12"),
    ("勉县服务区·充电站", 336, "charge", 240, 1.60, 0.0, "0916-3200xxx", "枪位 6"),
    ("宁强服务区·充电站", 392, "charge", 180, 1.66, 0.0, "0916-4300xxx", "枪位 4"),
    ("广元服务区·充电站", 452, "charge", 240, 1.59, 0.0, "0839-3300xxx", "★ 落在雨带内"),
    ("广元城区·物流园充电站", 452, "charge", 360, 1.48, 6.4, "0839-3355xxx", "绕行 6.4km · 大功率"),
    ("剑门关服务区·充电站", 508, "charge", 240, 1.63, 0.0, "0839-6600xxx", "雨带边缘"),
    ("江油服务区·充电站", 578, "charge", 240, 1.57, 0.0, "0816-3600xxx", "枪位 8"),
    ("绵阳服务区·充电站", 634, "charge", 300, 1.54, 0.0, "0816-2200xxx", "枪位 10"),
    ("德阳服务区·充电站", 682, "charge", 240, 1.56, 0.0, "0838-2500xxx", "枪位 8"),
    ("成都·青白江物流园", 726, "charge", 360, 1.46, 0.0, "028-8300xxxx", "终点充电场"),
]


def build_stations() -> list[dict]:
    out = []
    for i, (name, km, typ, kw, price, detour, tel, note) in enumerate(STATIONS, start=1):
        out.append({
            "id": f"st-{i:03d}",
            "name": name,
            "km": float(km),
            "type": typ,
            "power_kw": kw,
            "price_yuan_kwh": price,
            "detour_km": detour,
            "telephone": tel,
            "note": note,
            # 诚实标注来源：这一版是离线示意数据，不是线上 POI 抓取结果
            "source": "curated",
            "verified": False,
        })
    return out


# ─────────────────────────────────────────────────────────────
# 7. 车辆与情景
# ─────────────────────────────────────────────────────────────
VEHICLE = {
    "type": "electric_hgv",
    "label": "电动重卡 · 6×4 牵引车",
    "mass_kg": 49000,
    "battery_kwh": 900.0,
    "soc0": 0.90,
    "soc_warn": 0.30,
    "soc_reserve": 0.12,
    "eta_drive": 0.90,
    "eta_recup": 0.60,
    # 以下系数是"待标定的默认值"：页面必须标明"模型估算"，见 docs/提交材料.md
    "crr_kwh_per_100km_per_t": 2.6,
    "drag_kwh_per_100km_per_ms2": 0.069,
    "hvac_kwh_per_100km_per_dc": 0.9,
    "rain_kwh_per_100km_per_mmh": 0.35,
    "v_opt_kmh": 75.0,
    "temp_comfort_c": 22.0,
}

SCENARIOS = [
    {
        "id": "s1-0800",
        "title": "08:00 出发 · 撞上雨带",
        "depart_hour": 8.0,
        "soc0": 0.90,
        "mass_kg": 49000,
        "story": "按计划出发：到广元时正值雨带最强时段，且电量同时逼近告警线。",
        "expect": {},
    },
    {
        "id": "s2-0400",
        "title": "04:00 出发 · 抢在雨前",
        "depart_hour": 4.0,
        "soc0": 0.90,
        "mass_kg": 49000,
        "story": "同样的车、同样的路，只是早走 3 小时：雨带还没起来，风险暴露接近 0。",
        "expect": {},
    },
    {
        "id": "s3-heavy",
        "title": "满载重车 · 低电量出发",
        "depart_hour": 8.0,
        "soc0": 0.35,
        "mass_kg": 55000,
        "story": "重载 + 出车电量低：即使按最优方案也无法一程到达，必须给出「不可行」的诚实结论与建议。",
        "expect": {},
    },
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="data")
    args = ap.parse_args()
    out = Path(args.outdir)
    (out / "curated").mkdir(parents=True, exist_ok=True)

    # ── 干线几何 ──
    wgs_anchors = [(a[1], a[2]) for a in ANCHORS]
    dense = catmull_rom(wgs_anchors, per_seg=PER_SEG)
    cum = cumulative_km(dense)
    total_km = cum[-1]
    samples = resample_along(dense, cum, SAMPLE_STEP_KM)

    # Catmull-Rom 每段输出 PER_SEG 个点，因此第 j 个控制点落在 dense 的 j*PER_SEG 号位置
    anchor_km = [(cum[min(i * PER_SEG, len(cum) - 1)], a[3]) for i, a in enumerate(ANCHORS)]
    anchor_km.sort()

    # ── BD-09 输出（百度地图用） ──
    poly_bd = []
    for lng, lat in dense:
        blng, blat = wgs84_to_bd09(lng, lat)
        poly_bd.append([round(blng, 6), round(blat, 6)])

    points = []
    for s in samples:
        blng, blat = wgs84_to_bd09(*s["wgs"])
        elev = elevation_at(s["km"], anchor_km)
        nearest_anchor = min(anchor_km, key=lambda ak: abs(ak[0] - s["km"]))
        points.append({
            "km": round(s["km"], 2),
            "bd": [round(blng, 6), round(blat, 6)],
            "elev_m": round(elev, 1),
            "lowland": lowland_weight(s["km"]),
            "near_anchor_km": round(nearest_anchor[0], 1),
        })

    # 沿途最近的补给站（用于剖面图标注）
    stations = build_stations()
    for p in points:
        near = min(stations, key=lambda st: abs(st["km"] - p["km"]))
        p["near_station"] = near["id"] if abs(near["km"] - p["km"]) <= 12 else None

    corridor = {
        "id": "xg-cd-g5",
        "name": "西安 → 成都（G5 京昆高速）",
        "note": "干线中心线为离线生成的平滑示意线；在线模式下由百度驾车路线规划实测替换。",
        "crs": "BD-09",
        "total_km": round(total_km, 1),
        "depart_date": "2026-07-15",
        "polyline": poly_bd,
        "anchors": [{"name": a[0], "km": round(cum[i], 1),
                     "bd": [round(v, 6) for v in wgs84_to_bd09(a[1], a[2])],
                     "elev_m": a[3]} for i, a in enumerate(ANCHORS)],
        "points": points,
        "vehicle": VEHICLE,
    }

    # ── 气象缓存 ──
    weather = {
        "generated_by": "tools/make_demo_data.py",
        "kind": "simulated",
        "disclaimer": "离线仿真缓存，用于保证 Demo 与视频可复现；接入线上天气查询后字段不变。",
        "grid": [{"km": p["km"], "bd": p["bd"],
                  "hours": [weather_at(p["km"], h) for h in range(HOURS)]} for p in points],
    }

    # ── 坡度/低洼字典（自建模型，明示为模型假设）──
    terrain = {
        "source": "model",
        "disclaimer": "百度基础 WebAPI 无批量高程接口；本文件是自建的坡度字典，属模型假设，非实测。",
        "lowland_falloff_km": LOWLAND_FALLOFF_KM,
        "lowland_bands": [{"km0": a, "km1": b, "weight": w} for a, b, w in LOWLAND],
        "profile": [{"km": p["km"], "elev_m": p["elev_m"], "lowland": p["lowland"]}
                    for p in points],
        "total_km": round(total_km, 1),
    }

    (out / "corridors.json").write_text(
        json.dumps({"corridors": [corridor]}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "weather_cache.json").write_text(
        json.dumps(weather, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "terrain.json").write_text(
        json.dumps(terrain, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "stations.json").write_text(
        json.dumps({"source": "curated", "disclaimer":
                    "补给站为依公开资料整理的示意数据，未用地点检索核验；"
                    "在线模式应改用 tools/fetch_stations.py 的真实 POI 结果。",
                    "stations": stations}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "scenarios.json").write_text(
        json.dumps({"corridor_id": corridor["id"], "scenarios": SCENARIOS},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 设置文件：AK 与页面参数（与 ohbj 同构） ──
    print(f"干线总里程 {total_km:.1f} km ｜ 采样点 {len(points)} 个（步长 {SAMPLE_STEP_KM:.0f} km）")
    print(f"海拔 最低 {min(p['elev_m'] for p in points):.0f} m / 最高 {max(p['elev_m'] for p in points):.0f} m")
    print(f"雨带峰值 {BAND['peak_mm_h']:.0f} mm/h @ {BAND['center_km']:.0f} km / {BAND['center_h']:.0f}:00")
    for f in ("corridors.json", "weather_cache.json", "terrain.json", "stations.json", "scenarios.json"):
        p = out / f
        print(f"  {p}  {p.stat().st_size / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
