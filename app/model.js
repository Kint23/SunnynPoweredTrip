/* 晴好出发 · 预测与优化模型（纯函数 · 无 DOM · 浏览器 / Node 双用）
 *
 * 为什么单独成文件：模型必须能**脱离地图和浏览器单测**。
 *   - 浏览器里： window.SPTModel.simulate(...)
 *   - Node 里：  require('./app/model.js')  →  tools/run_model.mjs / tools/validate.mjs
 * 这样校验脚本才能重算「金标」数值，防止改模型时悄悄改坏结论。
 *
 * 三条关键建模决策（都有理由，不是随手写的）：
 *   1. **积水 ≠ 雨大**：降水只有落在低洼地面才会形成高风险积水。所以 lowland 是"闸门"，
 *      离开干线走向高地（绕行）能真正降低风险 —— 否则"绕行"在模型里毫无意义。
 *   2. **几何用投影坐标**：绕行路线上的点要投影回干线，才能同时拿到
 *      "这是哪一段"（base_km）与"横向偏出去多少公里"（lateral_km）。
 *   3. **迭代收敛**：速度 → 到达时刻 → 天气 → 速度 是个环，跑 4 轮不动点。
 *
 * 公式与假设详见 docs/方案与计划.md §5。所有系数都可由调用方覆盖；
 * 页面必须标明"模型估算"，不得表述为实测结果。
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.SPTModel = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var G = 9.81;              // m/s^2
  var J_PER_KWH = 3.6e6;

  var DEFAULTS = {
    tau: 0.5,                 // 高风险判定阈值
    rain_crit: 30,            // mm/h，PRD 给的阈值
    cum_rain_crit: 60,        // mm，最近数小时累计
    w_pond: 0.75,             // 积水项权重
    w_sat: 0.25,              // 地表饱和项权重
    lowland_floor: 0.03,      // 即使不是低洼路段也有 3% 的积水倾向（排水良好的高速）
    lowland_falloff_km: 5,    // 横向离开干线多少公里后，低洼权重衰减到 0
    risk_window_h: 3,         // 只看最近 3 小时——地面积水是短时强降水的事
    v_floor: 30,
    v_ceil: 92,
    slow_detour: 0.72,        // 走并行国省道的速度折减
    // 「综合代价」权重：把时间与风险都折算成 km，便于排序与解释
    w_time_km_per_min: 1.25,  // 1 分钟 ≈ 1.25 km（≈ 75 km/h）
    w_risk_km: 5.0,           // 风险厌恶系数：1 km 的满风险暴露 ≈ 5 km 代价（可调）
    w_price_km_per_yuan: 2.5,
    charge_overhead_min: 8,   // 进出站 / 插枪 / 结算
    charge_efficiency: 0.92,
    max_charge_stops: 1       // v1 约束：单程最多补能 1 次（时效与司机排班）
  };

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function round(v, n) { var p = Math.pow(10, n || 0); return Math.round(v * p) / p; }

  function haversineKm(a, b) {
    var R = 6371.0088;
    var p1 = a[1] * Math.PI / 180, p2 = b[1] * Math.PI / 180;
    var dp = p2 - p1, dl = (b[0] - a[0]) * Math.PI / 180;
    var h = Math.sin(dp / 2) * Math.sin(dp / 2) +
            Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) * Math.sin(dl / 2);
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  function bearingDeg(a, b) {
    var p1 = a[1] * Math.PI / 180, p2 = b[1] * Math.PI / 180;
    var dl = (b[0] - a[0]) * Math.PI / 180;
    var y = Math.sin(dl) * Math.cos(p2);
    var x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
    return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
  }

  function cumulativeKm(poly) {
    var cum = [0];
    for (var i = 1; i < poly.length; i++) cum.push(cum[i - 1] + haversineKm(poly[i - 1], poly[i]));
    return cum;
  }

  /* 把一个点投影到干线：返回「干线里程」与「横向偏出距离(km)」。
   * 局部平面近似足够——尺度只有几十公里。 */
  function projectToBase(poly, cum, p) {
    var best = { km: 0, lateral_km: Infinity };
    var lat0 = p[1] * Math.PI / 180;
    var kx = 111.32 * Math.cos(lat0), ky = 110.57;
    for (var i = 1; i < poly.length; i++) {
      var a = poly[i - 1], b = poly[i];
      var ax = (a[0] - p[0]) * kx, ay = (a[1] - p[1]) * ky;
      var bx = (b[0] - p[0]) * kx, by = (b[1] - p[1]) * ky;
      var dx = bx - ax, dy = by - ay;
      var len2 = dx * dx + dy * dy;
      var t = len2 <= 0 ? 0 : clamp(-(ax * dx + ay * dy) / len2, 0, 1);
      var qx = ax + dx * t, qy = ay + dy * t;
      var d = Math.sqrt(qx * qx + qy * qy);
      if (d < best.lateral_km) {
        best.lateral_km = d;
        best.km = cum[i - 1] + (cum[i] - cum[i - 1]) * t;
      }
    }
    return best;
  }

  /* ── 气象：在 (里程, 时刻) 上做双线性插值 ───────────────────────── */
  function gridAt(grid, km) {
    if (!grid.length) return null;
    var i = 0;
    while (i < grid.length - 2 && grid[i + 1].km < km) i++;
    var g0 = grid[i], g1 = grid[Math.min(i + 1, grid.length - 1)];
    var span = Math.max(1e-9, g1.km - g0.km);
    return { g0: g0, g1: g1, t: clamp((km - g0.km) / span, 0, 1) };
  }

  function hourAt(entry, th) {
    var n = entry.hours.length;
    var x = ((th % n) + n) % n;
    var i = Math.floor(x), j = (i + 1) % n, f = x - i;
    var a = entry.hours[i], b = entry.hours[j];
    return {
      rain_mm_h: a.rain_mm_h + (b.rain_mm_h - a.rain_mm_h) * f,
      wind_ms: a.wind_ms + (b.wind_ms - a.wind_ms) * f,
      wind_dir_deg: a.wind_dir_deg + (b.wind_dir_deg - a.wind_dir_deg) * f,
      vis_km: a.vis_km + (b.vis_km - a.vis_km) * f,
      temp_c: a.temp_c + (b.temp_c - a.temp_c) * f
    };
  }

  function weatherAt(grid, km, th) {
    var b = gridAt(grid, km);
    if (!b) return null;
    var w0 = hourAt(b.g0, th), w1 = hourAt(b.g1, th);
    return {
      rain_mm_h: w0.rain_mm_h + (w1.rain_mm_h - w0.rain_mm_h) * b.t,
      wind_ms: w0.wind_ms + (w1.wind_ms - w0.wind_ms) * b.t,
      wind_dir_deg: w0.wind_dir_deg + (w1.wind_dir_deg - w0.wind_dir_deg) * b.t,
      vis_km: w0.vis_km + (w1.vis_km - w0.vis_km) * b.t,
      temp_c: w0.temp_c + (w1.temp_c - w0.temp_c) * b.t
    };
  }

  function cumRain(grid, km, th, hours) {
    var b = gridAt(grid, km);
    if (!b) return 0;
    var sum = 0;
    for (var k = 1; k <= hours; k++) {
      var w0 = hourAt(b.g0, th - k), w1 = hourAt(b.g1, th - k);
      sum += w0.rain_mm_h + (w1.rain_mm_h - w0.rain_mm_h) * b.t;
    }
    return sum;
  }

  /* ── 低洼权重：由「干线里程(在哪一段)」+「横向偏出(有没有离开河谷)」共同决定 ── */
  function lowlandAt(terrain, baseKm, lateralKm) {
    var w = 0;
    var bands = terrain.lowland_bands || [];
    for (var i = 0; i < bands.length; i++) {
      var b = bands[i];
      if (baseKm >= b.km0 && baseKm <= b.km1 && b.weight > w) w = b.weight;
    }
    if (w <= 0) return 0;
    var R = terrain.lowland_falloff_km || 5;
    var d = Math.min(lateralKm, R);
    var f = 0.5 * (1 + Math.cos(Math.PI * d / R));   // 平滑衰减：贴着干线 = 1，偏出 R 后 = 0
    return round(w * f, 3);
  }

  function elevAtProfile(profile, km) {
    var n = profile.length;
    if (!n) return 0;
    if (km <= profile[0].km) return profile[0].elev_m;
    for (var i = 1; i < n; i++) {
      if (km <= profile[i].km) {
        var a = profile[i - 1], b = profile[i];
        var t = (km - a.km) / Math.max(1e-9, b.km - a.km);
        return round(a.elev_m + (b.elev_m - a.elev_m) * t, 1);
      }
    }
    return profile[n - 1].elev_m;
  }

  /* ── 风险 = 积水项（雨 × 低洼闸门）+ 饱和项 ─────────────────────── */
  function riskOf(rain, cum, lowland, opt) {
    var gate = opt.lowland_floor + (1 - opt.lowland_floor) * lowland;
    var pond = clamp(rain / opt.rain_crit, 0, 1.4) * gate;
    var sat = clamp(cum / opt.cum_rain_crit, 0, 1.2) * lowland;
    return clamp(opt.w_pond * pond + opt.w_sat * sat, 0, 1);
  }

  /* ── 车速：雨 → 慢，上坡 → 慢，低能见度 → 慢，绕行段 → 慢 ───────── */
  function speedAt(vOpt, rain, grade, vis, slow) {
    var fRain = 1 - 0.35 * clamp(rain / 40, 0, 1);
    var fGrade = 1 - 0.28 * clamp(Math.max(0, grade) / 0.06, 0, 1);
    var fVis = vis < 5 ? 0.85 : 1;
    return clamp(vOpt * fRain * fGrade * fVis * (slow || 1), 30, 92);
  }

  /* ── 能耗：坡度(势能) + 滚阻 + 风阻 + 空调 + 雨阻 ──────────────── */
  function segmentEnergy(ds, dh, vKmh, rain, temp, mass, veh, headwindMs) {
    var eta = veh.eta_drive, rec = veh.eta_recup;
    var grade = dh > 0
      ? (mass * G * dh) / (J_PER_KWH * eta)
      : -rec * (mass * G * Math.abs(dh)) / J_PER_KWH;
    var roll = (ds / 100) * veh.crr_kwh_per_100km_per_t * (mass / 1000);
    var vms = vKmh / 3.6;
    var hw = Math.max(-0.7 * vms, headwindMs);   // 顺风不能把阻力算成负收益
    var drag = (ds / 100) * veh.drag_kwh_per_100km_per_ms2 * Math.pow(vms + hw, 2);
    var hvac = (ds / 100) * veh.hvac_kwh_per_100km_per_dc * Math.abs(temp - veh.temp_comfort_c);
    var rainE = (ds / 100) * veh.rain_kwh_per_100km_per_mmh * rain;
    return { grade: grade, roll: roll, drag: drag, hvac: hvac, rain: rainE,
             total: grade + roll + drag + hvac + rainE };
  }

  /* ── 把任意几何重采样成算法用的点序列（自带海拔 / 低洼 / 慢行系数） ── */
  function preparePoints(poly, ctx, n) {
    var cum = cumulativeKm(poly);
    var total = cum[cum.length - 1];
    var cnt = n || 29;
    var pts = [], j = 0;
    for (var s = 0; s < cnt; s++) {
      var d = total * s / (cnt - 1);
      while (j < cum.length - 2 && cum[j + 1] < d) j++;
      var seg = Math.max(1e-9, cum[j + 1] - cum[j]);
      var t = clamp((d - cum[j]) / seg, 0, 1);
      var bd = [poly[j][0] + (poly[j + 1][0] - poly[j][0]) * t,
                poly[j][1] + (poly[j + 1][1] - poly[j][1]) * t];
      var pr = projectToBase(ctx.basePolyline, ctx.baseCum, bd);
      pts.push({
        km: round(d, 3),
        base_km: round(pr.km, 3),
        lateral_km: round(pr.lateral_km, 3),
        bd: [round(bd[0], 6), round(bd[1], 6)],
        elev_m: elevAtProfile(ctx.terrain.profile, pr.km),
        lowland: lowlandAt(ctx.terrain, pr.km, pr.lateral_km),
        slow: pr.lateral_km > 0.5 ? ctx.opt.slow_detour : 1
      });
    }
    return pts;
  }

  /* ── 沿点序列跑完整仿真 ───────────────────────────────────────────
   * pause（可选）：在某个里程处停车等待若干分钟 —— 用来评估「服务区避雨」这一策略。
   * 注意：等待必须写进时间轴（times），否则候选方案会"声称等了 60 分钟"却毫无代价 ——
   * 这种"标签与实际不符"是最危险的一类 bug，validate.mjs 里有不变量盯着它。
   */
  function runTrace(points, grid, vehicle, departHour, soc0, mass, opt, pause) {
    var n = points.length;
    var tr = { km: [], time: [], speed: [], rain: [], risk: [], cum_rain: [], lowland: [],
               energy: [], soc: [], grade: [], headwind: [], temp: [], vis: [],
               breakdown: { grade: 0, roll: 0, drag: 0, hvac: 0, rain: 0 } };

    // 迭代收敛：速度 ← 雨 ← 到达时刻 ← 速度
    var times = new Array(n).fill(departHour);
    var speeds = new Array(n).fill(opt.v_floor);
    for (var pass = 0; pass < 4; pass++) {
      var prev = times.slice();
      var t = departHour;
      var paused = false;
      for (var i = 0; i < n; i++) {
        var w = weatherAt(grid, points[i].base_km, t) ||
                { rain_mm_h: 0, wind_ms: 0, wind_dir_deg: 0, vis_km: 20, temp_c: 22 };
        var ds = i === 0 ? 0 : points[i].km - points[i - 1].km;
        var grade = (i === 0 || ds <= 0) ? 0 : (points[i].elev_m - points[i - 1].elev_m) / (ds * 1000);
        speeds[i] = speedAt(vehicle.v_opt_kmh, w.rain_mm_h, grade, w.vis_km, points[i].slow);
        times[i] = t;
        if (i < n - 1) {
          t += (points[i + 1].km - points[i].km) / speeds[i];
          if (pause && !paused && points[i + 1].km >= pause.km) {
            t += pause.minutes / 60;
            paused = true;
          }
        }
      }
      var done = true;
      for (var k = 0; k < n; k++) if (Math.abs(times[k] - prev[k]) > 0.02) { done = false; break; }
      if (done) break;
    }
    tr.pause_min = (pause && pause.minutes) || 0;

    var soc = soc0;
    for (var i2 = 0; i2 < n; i2++) {
      var ds2 = i2 === 0 ? 0 : points[i2].km - points[i2 - 1].km;
      var dh = i2 === 0 ? 0 : points[i2].elev_m - points[i2 - 1].elev_m;
      var th = times[i2];
      var w2 = weatherAt(grid, points[i2].base_km, th) ||
               { rain_mm_h: 0, wind_ms: 0, wind_dir_deg: 0, vis_km: 20, temp_c: 22 };
      var cum = cumRain(grid, points[i2].base_km, th, opt.risk_window_h);
      var risk = riskOf(w2.rain_mm_h, cum, points[i2].lowland || 0, opt);

      var head = 0;
      if (i2 < n - 1) {
        var brg = bearingDeg(points[Math.max(0, i2 - 1)].bd, points[Math.min(n - 1, i2 + 1)].bd);
        head = w2.wind_ms * Math.cos((w2.wind_dir_deg - brg) * Math.PI / 180);
      }
      var e = i2 === 0 ? { grade: 0, roll: 0, drag: 0, hvac: 0, rain: 0, total: 0 }
        : segmentEnergy(ds2, dh, speeds[i2], w2.rain_mm_h, w2.temp_c, mass, vehicle, head);
      if (i2 > 0) {
        tr.breakdown.grade += e.grade; tr.breakdown.roll += e.roll;
        tr.breakdown.drag += e.drag; tr.breakdown.hvac += e.hvac; tr.breakdown.rain += e.rain;
      }
      soc -= e.total / vehicle.battery_kwh;

      tr.km.push(round(points[i2].km, 2));
      tr.time.push(round(th, 3));
      tr.speed.push(round(speeds[i2], 1));
      tr.rain.push(round(w2.rain_mm_h, 2));
      tr.risk.push(round(risk, 3));
      tr.cum_rain.push(round(cum, 1));
      tr.lowland.push(points[i2].lowland);
      tr.energy.push(round(e.total, 3));
      tr.soc.push(round(soc, 4));
      tr.grade.push(round(ds2 <= 0 ? 0 : dh / (ds2 * 1000), 4));
      tr.headwind.push(round(head, 2));
      tr.temp.push(round(w2.temp_c, 1));
      tr.vis.push(round(w2.vis_km, 1));
    }
    for (var kk in tr.breakdown) tr.breakdown[kk] = round(tr.breakdown[kk], 1);
    tr.total_km = round(points[n - 1].km - points[0].km, 2);
    tr.minutes = round((times[n - 1] - departHour) * 60, 1);
    tr.energy_kwh = round(tr.breakdown.grade + tr.breakdown.roll + tr.breakdown.drag +
                          tr.breakdown.hvac + tr.breakdown.rain, 1);
    tr.soc_end = round(soc, 4);
    return tr;
  }

  function hazardSegments(trace, opt) {
    var segs = [], cur = null;
    for (var i = 0; i < trace.km.length; i++) {
      if (trace.risk[i] >= opt.tau) {
        if (!cur) cur = { km0: trace.km[i], km1: trace.km[i], peak_risk: trace.risk[i],
                          peak_rain: trace.rain[i], i0: i, i1: i };
        cur.km1 = trace.km[i];
        if (trace.risk[i] > cur.peak_risk) cur.peak_risk = trace.risk[i];
        if (trace.rain[i] > cur.peak_rain) cur.peak_rain = trace.rain[i];
        cur.i1 = i;
      } else if (cur) { segs.push(cur); cur = null; }
    }
    if (cur) segs.push(cur);
    return segs;
  }

  function riskExposure(trace) {
    var acc = 0;
    for (var i = 1; i < trace.km.length; i++) {
      acc += 0.5 * (trace.risk[i] + trace.risk[i - 1]) * (trace.km[i] - trace.km[i - 1]);
    }
    return round(acc, 2);
  }

  /* ── 绕行几何：高风险段走上并行国省道（法向偏移 + 两端平滑过渡） ──
   * 官方接口没有 avoid_polygons（见 docs/方案与计划.md §0.1-B），
   * 因此这里生成"绕行示意几何"；在线模式应由驾车路线规划实测替换。
   */
  function detourPolyline(polyline, cum, km0, km1, offsetKm, rampKm) {
    var out = [], n = polyline.length;
    for (var i = 0; i < n; i++) {
      var km = cum[i];
      var amp = 0;
      if (km > km0 - rampKm && km < km1 + rampKm) {
        var a = clamp((km - (km0 - rampKm)) / rampKm, 0, 1);
        var b = clamp(((km1 + rampKm) - km) / rampKm, 0, 1);
        amp = Math.min(a, b);
        amp = amp * amp * (3 - 2 * amp);            // smoothstep，避免折角
      }
      if (amp <= 0) { out.push(polyline[i].slice()); continue; }
      var j = Math.min(n - 1, i + 1), k = Math.max(0, i - 1);
      // 航向单位向量（东, 北）= (sinθ, cosθ)；其左侧法向 = (cosθ, -sinθ)。
      // 注意：不能写成 (cos(θ+90°), sin(θ+90°)) —— 那是把 (东,北) 当成 (x,y) 直角坐标系的写法，
      // 在 (东,北) 约定下会得到"部分沿航向"的位移，绕行会失效（本项目踩过）。
      var th = bearingDeg(polyline[k], polyline[j]) * Math.PI / 180;
      var off = offsetKm * amp;
      var dEast = off * Math.cos(th);
      var dNorth = -off * Math.sin(th);
      out.push([polyline[i][0] + dEast / (111.32 * Math.cos(polyline[i][1] * Math.PI / 180)),
                polyline[i][1] + dNorth / 110.57]);
    }
    return out;
  }

  /* ── 补给站评价与排序 ───────────────────────────────────────────── */
  function nearestIdx(trace, km) {
    var best = 0, bd = Infinity;
    for (var i = 0; i < trace.km.length; i++) {
      var d = Math.abs(trace.km[i] - km);
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }

  function rankStations(stations, trace, opt, refPrice) {
    var warnIdx = -1;
    for (var i = 0; i < trace.soc.length; i++) {
      if (trace.soc[i] <= opt.soc_warn) { warnIdx = i; break; }
    }
    if (warnIdx < 0) warnIdx = trace.soc.length - 1;
    var warnKm = trace.km[warnIdx], warnSoc = trace.soc[warnIdx];

    var out = [];
    for (var s = 0; s < stations.length; s++) {
      var st = stations[s];
      var idx = nearestIdx(trace, st.km);
      // 已经开过去的站不可能再回头去充—
      var behind = st.km < warnKm - 2;

      var toStation = 0;
      for (var k = warnIdx + 1; k <= idx; k++) toStation += trace.energy[k];
      var socOnArrival = warnSoc - toStation / opt.battery_kwh;

      var toEnd = 0;
      for (var m = idx; m < trace.km.length; m++) toEnd += trace.energy[m];

      var needKwh = Math.max(0, toEnd + opt.soc_reserve * opt.battery_kwh -
                                socOnArrival * opt.battery_kwh);
      var capKwh = Math.max(0, (opt.soc_target - socOnArrival) * opt.battery_kwh);
      var chargeKwh = Math.min(needKwh, capKwh);

      var dwellMin = opt.charge_overhead_min +
                     (chargeKwh / (st.power_kw * opt.charge_efficiency)) * 60;
      var equiv = st.detour_km * 2 +
                  dwellMin * opt.w_time_km_per_min +
                  trace.risk[idx] * 100 * (opt.w_risk_km / 100) +
                  Math.max(0, st.price_yuan_kwh - refPrice) * chargeKwh * opt.w_price_km_per_yuan;

      out.push({
        id: st.id, name: st.name, km: st.km, detour_km: st.detour_km,
        power_kw: st.power_kw, price_yuan_kwh: st.price_yuan_kwh,
        telephone: st.telephone, note: st.note, source: st.source,
        soc_on_arrival: round(socOnArrival, 4),
        risk_there: round(trace.risk[idx], 3),
        arrive_hour: round(trace.time[idx], 2),
        need_kwh: round(chargeKwh, 1),
        dwell_min: round(dwellMin, 1),
        behind: behind,
        feasible: !behind && needKwh <= capKwh + 1e-6 && socOnArrival >= opt.soc_reserve,
        cost: round(behind ? equiv + 100000 : equiv, 2)
      });
    }
    out.sort(function (a, b) {
      if (a.feasible !== b.feasible) return a.feasible ? -1 : 1;
      return a.cost - b.cost;
    });
    return { warn_km: round(warnKm, 2), warn_soc: round(warnSoc, 4),
             warn_hour: round(trace.time[warnIdx], 2), ranked: out };
  }

  function mkCandidate(id, label, trace, station, detourKm, opt) {
    var stops = station ? [{
      id: station.id, name: station.name, km: station.km, detour_km: station.detour_km,
      need_kwh: station.need_kwh, dwell_min: station.dwell_min,
      arrive_hour: station.arrive_hour, soc_on_arrival: station.soc_on_arrival,
      risk_there: station.risk_there, power_kw: station.power_kw,
      price_yuan_kwh: station.price_yuan_kwh, telephone: station.telephone, note: station.note
    }] : [];
    var dwell = stops.reduce(function (a, s) { return a + s.dwell_min; }, 0);

    // 把"补能"真正反映到 SOC 曲线上：从该站起整体抬升，剖面图才能看到锯齿
    var soc = trace.soc.slice();
    if (station) {
      var idx = nearestIdx(trace, station.km);
      var add = station.need_kwh / opt.battery_kwh;
      for (var i = idx; i < soc.length; i++) soc[i] = round(soc[i] + add, 4);
    }

    return {
      id: id, label: label,
      total_km: trace.total_km,
      minutes: round(trace.minutes + dwell, 1),
      drive_minutes: trace.minutes,
      energy_kwh: trace.energy_kwh,
      soc_end: round(soc[soc.length - 1], 4),
      soc_min: round(Math.min.apply(null, soc), 4),
      soc: soc,
      km: trace.km,
      risk_exposure_km: riskExposure(trace),
      detour_km: round(detourKm, 1),
      dwell_min: round(dwell, 1),
      wait_min: trace.pause_min || 0,   // 唯一真相源：时长里真的加了这段等待
      stops: stops,
      cost_yuan: round(stops.reduce(function (a, s) {
        return a + s.need_kwh * s.price_yuan_kwh;
      }, 0), 1),
      score: 0,
      trace: trace,
      chosen: false
    };
  }

  /* ── 主入口 ────────────────────────────────────────────────────── */
  function simulate(input) {
    var opt = {};
    for (var k in DEFAULTS) opt[k] = DEFAULTS[k];
    for (var k2 in (input.options || {})) opt[k2] = input.options[k2];

    var veh = {};
    for (var k4 in input.corridor.vehicle) veh[k4] = input.corridor.vehicle[k4];
    for (var k3 in (input.vehicle || {})) veh[k3] = input.vehicle[k3];

    var battery = veh.battery_kwh;
    opt.battery_kwh = battery;
    opt.soc_warn = veh.soc_warn;
    opt.soc_reserve = veh.soc_reserve;
    opt.soc_target = Math.min(1.0, veh.soc_warn + 0.70);   // 快速充电桩普遍充到 90%+

    var depart = input.depart_hour, soc0 = input.soc0, mass = input.mass_kg;
    var basePoly = input.corridor.polyline;
    var baseCum = cumulativeKm(basePoly);
    var grid = input.weather.grid;
    var terrain = input.terrain;

    var ctx = { basePolyline: basePoly, baseCum: baseCum, terrain: terrain, opt: opt };
    var basePoints = preparePoints(basePoly, ctx, 29);

    var base = runTrace(basePoints, grid, veh, depart, soc0, mass, opt);
    base.hazard = hazardSegments(base, opt);
    base.risk_exposure_km = riskExposure(base);

    var refPrice = 1.60;
    var stations = rankStations(input.stations, base, opt, refPrice);
    var feasible = stations.ranked.filter(function (s) { return s.feasible; });

    var candidates = [];

    // A：就地补能（不动路线）
    if (feasible.length) {
      candidates.push(mkCandidate("A", "就地补能 · 不绕行", base, feasible[0], 0, opt));
    }

    // B：绕开积水段（并行国省道）+ 补能
    if (base.hazard.length) {
      var hz = base.hazard[0];
      var off = 6, margin = 10;  // off > lowland_falloff_km：绕行段完全离开低洼带
      var polyB = detourPolyline(basePoly, baseCum, hz.km0 - margin, hz.km1 + margin, off, 6);
      var ptsB = preparePoints(polyB, ctx, 29);
      var traceB = runTrace(ptsB, grid, veh, depart, soc0, mass, opt);
      traceB.hazard = hazardSegments(traceB, opt);
      traceB.risk_exposure_km = riskExposure(traceB);
      var stB = rankStations(input.stations, traceB, opt, refPrice);
      var bestB = stB.ranked.filter(function (s) { return s.feasible; })[0];
      if (bestB) {
        var cB = mkCandidate("B", "绕开积水段（并行国省道）", traceB, bestB,
                            round(traceB.total_km - base.total_km, 1), opt);
        cB.station_rank = stB;
        cB.points = ptsB;
        cB.polyline = polyB;
        candidates.push(cB);
      }
    }

    // C：服务区避雨等待（不绕行、不早补，用时间换风险）
    if (base.hazard.length) {
      var hzKm0 = base.hazard[0].km0;
      var anchor = null;
      for (var i3 = 0; i3 < stations.ranked.length; i3++) {
        var cand = stations.ranked[i3];
        if (cand.feasible && cand.km <= hzKm0 + 5) { anchor = cand; break; }
      }
      if (anchor) {
        // 等多久本身也是一个决策变量：逐个试，取综合代价最低的那个
        var waits = [60, 120, 180, 240, 300, 360];
        var bestC = null, bestScore = Infinity;
        for (var wI = 0; wI < waits.length; wI++) {
          var traceC = runTrace(basePoints, grid, veh, depart, soc0, mass, opt,
                                { km: anchor.km, minutes: waits[wI] });
          traceC.hazard = hazardSegments(traceC, opt);
          traceC.risk_exposure_km = riskExposure(traceC);
          var sc = (traceC.minutes + anchor.dwell_min) * opt.w_time_km_per_min +
                   traceC.risk_exposure_km * opt.w_risk_km;
          if (sc < bestScore) {
            bestScore = sc;
            bestC = mkCandidate("C", "服务区避雨等待 " + waits[wI] + " 分钟",
                                traceC, anchor, 0, opt);
          }
        }
        if (bestC) candidates.push(bestC);
      }
    }

    for (var q = 0; q < candidates.length; q++) {
      var c = candidates[q];
      // 综合代价（折算里程）＝ 全部耗时（含驾驶/补能/等待）× 时间权重 ＋ 风险暴露 × 风险厌恶
      c.score = round(c.minutes * opt.w_time_km_per_min +
                      c.risk_exposure_km * opt.w_risk_km, 2);
    }
    candidates.sort(function (a, b) { return a.score - b.score; });
    if (candidates.length) candidates[0].chosen = true;

    var socMinBase = Math.min.apply(null, base.soc);
    var verdict;
    if (!candidates.length) verdict = "infeasible";
    else if (base.hazard.length === 0 && socMinBase >= opt.soc_reserve) verdict = "ok";
    else verdict = "recharge-required";

    var chosen = candidates[0] || null;

    return {
      options: opt,
      vehicle: veh,
      depart_hour: depart,
      soc0: soc0,
      mass_kg: mass,
      base: base,
      base_points: basePoints,
      stations: stations,
      candidates: candidates,
      chosen_id: chosen ? chosen.id : null,
      decision: chosen,
      summary: {
        total_km: base.total_km,
        minutes: chosen ? chosen.minutes : base.minutes,
        energy_kwh: chosen ? chosen.energy_kwh : base.energy_kwh,
        soc_min: round(chosen ? chosen.soc_min : socMinBase, 4),
        soc_end: round(chosen ? chosen.soc_end : base.soc_end, 4),
        risk_exposure_km: chosen ? chosen.risk_exposure_km : base.risk_exposure_km,
        hazard_count: base.hazard.length,
        // 高风险段的"覆盖里程"：采样点之间的区间也算在内，否则会低估（相邻两点都算命中）
        hazard_span_km: base.hazard.length
          ? round(base.hazard.reduce(function (a, h) {
              return a + (h.km1 - h.km0) + (base.km[1] - base.km[0]);
            }, 0), 1) : 0,
        hazard_km0: base.hazard.length ? round(base.hazard[0].km0 - (base.km[1] - base.km[0]) / 2, 1) : null,
        hazard_km1: base.hazard.length ? round(base.hazard[0].km1 + (base.km[1] - base.km[0]) / 2, 1) : null,
        hazard_peak_rain: base.hazard.length
          ? round(Math.max.apply(null, base.hazard.map(function (h) { return h.peak_rain; })), 1) : 0,
        hazard_peak_risk: base.hazard.length
          ? round(Math.max.apply(null, base.hazard.map(function (h) { return h.peak_risk; })), 3) : 0,
        warn_km: stations.warn_km,
        warn_hour: stations.warn_hour,
        charge_stops: chosen ? chosen.stops.length : 0,
        detour_km: chosen ? chosen.detour_km : 0,
        verdict: verdict,
        score: chosen ? chosen.score : null
      }
    };
  }

  return {
    VERSION: "2.0.0",
    DEFAULTS: DEFAULTS,
    haversineKm: haversineKm,
    cumulativeKm: cumulativeKm,
    projectToBase: projectToBase,
    preparePoints: preparePoints,
    lowlandAt: lowlandAt,
    elevAtProfile: elevAtProfile,
    weatherAt: weatherAt,
    riskOf: riskOf,
    runTrace: runTrace,
    hazardSegments: hazardSegments,
    riskExposure: riskExposure,
    detourPolyline: detourPolyline,
    rankStations: rankStations,
    simulate: simulate
  };
});
