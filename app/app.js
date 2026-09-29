/* 晴好出发 · 前端主程序
 *
 * 分工：model.js 算数（纯函数），app.js 只做「取数 → 调用 → 画图」。
 * 关键决定：页面**不直接调 WebAPI**，只读 data/*.json 缓存。
 *   原因见 docs/方案与计划.md §0.3：地点检索未认证仅 100 次/日，
 *   且演示视频必须可复现 —— 缓存是唯一同时满足这两点的做法。
 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var M = window.SPTModel;

  var C = {
    line: "#9aa7b4", lineHot: "#0a6ebd", danger: "#d93025", dangerSoft: "rgba(217,48,37,.18)",
    go: "#0f9d58", off: "#c2ccd6", warn: "#e8a33d", ink: "#16202b",
    rain: "#3aa0e8", elev: "#b9c4ce"
  };

  var S = { data: null, res: null, path: null, hour: 8, timer: null, useGL: false, map: null, view: null };

  /* ────────────────────────── 数据 ────────────────────────── */
  function loadJSON(p) {
    return fetch(p, { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(p + " → HTTP " + r.status);
      return r.json();
    });
  }

  function boot() {
    Promise.all([
      loadJSON("data/corridors.json"), loadJSON("data/weather_cache.json"),
      loadJSON("data/terrain.json"), loadJSON("data/stations.json"),
      loadJSON("data/scenarios.json")
    ]).then(function (a) {
      S.data = {
        corridor: a[0].corridors[0], weather: a[1], terrain: a[2],
        stations: a[3].stations, stationsMeta: a[3], scenarios: a[4].scenarios
      };
      S.baseCum = M.cumulativeKm(S.data.corridor.polyline);
      buildScenarioSelect();
      bindControls();
      recompute(true);
      initMap();
    }).catch(function (e) {
      $("mapTag").textContent = "数据加载失败：" + e.message;
      $("srcTag").textContent = "请用本地服务器打开（见 README）";
      $("srcTag").className = "badge off";
    });
  }

  function buildScenarioSelect() {
    var sel = $("scenario");
    sel.innerHTML = "";
    S.data.scenarios.forEach(function (sc) {
      var o = document.createElement("option");
      o.value = sc.id; o.textContent = sc.title;
      sel.appendChild(o);
    });
  }

  function currentScenario() {
    var id = $("scenario").value;
    return S.data.scenarios.filter(function (s) { return s.id === id; })[0] || S.data.scenarios[0];
  }

  function bindControls() {
    $("scenario").addEventListener("change", function () {
      var sc = currentScenario();
      $("depart").value = sc.depart_hour;
      $("soc0").value = Math.round(sc.soc0 * 100);
      $("mass").value = sc.mass_kg / 1000;
      recount();
      recompute(true);
    });
    ["depart", "soc0", "mass"].forEach(function (id) {
      $(id).addEventListener("input", recount);
      $(id).addEventListener("change", function () { recompute(true); });
    });
    $("recalc").addEventListener("click", function () { recompute(true); });
    $("time").addEventListener("input", function () {
      S.hour = parseFloat(this.value);
      paintTime();
      renderMap();
      renderProfile();
    });
    $("playBtn").addEventListener("click", togglePlay);

    var sc = currentScenario();
    $("depart").value = sc.depart_hour;
    $("soc0").value = Math.round(sc.soc0 * 100);
    $("mass").value = sc.mass_kg / 1000;
    recount();
  }

  function recount() {
    $("soc0v").textContent = $("soc0").value + "%";
    $("massv").textContent = $("mass").value + " t";
  }

  /* 当前"正在看"的方案：默认＝模型选中的那个，也可以手动点开对比 */
  function applyView() {
    var c = S.res.candidates.filter(function (x) { return x.id === S.view; })[0];
    S.path = {
      polyline: (c && c.polyline) || S.data.corridor.polyline,
      points: (c && c.points) || S.res.base_points,
      trace: (c && c.trace) || S.res.base,
      soc: (c && c.soc) || S.res.base.soc
    };
  }

  function selectCandidate(id) {
    S.view = id;
    applyView();
    paintTime();
    renderCandidates();
    renderWhy();
    renderMap();
    renderProfile();
  }

  /* ────────────────────────── 计算 ────────────────────────── */
  function recompute(resetTime) {
    var sc = currentScenario();
    var depart = parseFloat($("depart").value) || 0;
    var soc0 = (parseFloat($("soc0").value) || 90) / 100;
    var mass = (parseFloat($("mass").value) || 49) * 1000;

    if (resetTime) {
      S.hour = depart;
      $("time").value = depart;
    }

    S.res = M.simulate({
      corridor: S.data.corridor, weather: S.data.weather, terrain: S.data.terrain,
      stations: S.data.stations, depart_hour: depart, soc0: soc0, mass_kg: mass
    });
    S.res.scenario = sc;

    var ch = S.res.decision;
    S.view = ch ? ch.id : null;
    applyView();

    paintTime();
    renderVerdict();
    renderCandidates();
    renderStations();
    renderWhy();
    renderMap();
    renderProfile();
  }

  /* ────────────────────────── 文案 ────────────────────────── */
  function fmtHM(h) {
    var x = ((h % 24) + 24) % 24;
    var hh = Math.floor(x), mm = Math.round((x - hh) * 60);
    if (mm === 60) { hh += 1; mm = 0; }
    return (hh < 10 ? "0" : "") + hh + ":" + (mm < 10 ? "0" : "") + mm;
  }

  function paintTime() {
    $("clock").textContent = fmtHM(S.hour);
    var depart = S.res.depart_hour;
    var t = S.path.trace;
    var atEnd = t.time[t.time.length - 1];
    $("tHint").textContent = S.hour < depart ? "车辆尚未出发（" + fmtHM(depart) + " 发车）"
      : S.hour > atEnd ? "车辆已抵达终点" : "车辆在途 · 拖动查看雨带随时间的变化";
  }

  function renderVerdict() {
    var s = S.res.summary, ch = S.res.decision;
    var tag = $("vTag"), why = $("vWhy");
    if (s.verdict === "ok") { tag.className = "tag ok"; tag.textContent = "无需补能"; }
    else if (s.verdict === "recharge-required") { tag.className = "tag warn"; tag.textContent = "需补能 1 次"; }
    else { tag.className = "tag bad"; tag.textContent = "当前条件不可行"; }

    if (s.verdict === "infeasible") {
      why.innerHTML = "按「单程最多补能 1 次」的排班约束，这趟<b>跑不完</b>："
        + "总能耗 " + s.energy_kwh + " kWh，而 1 次补能最多只能补到 95% 电量。";
    } else {
      why.innerHTML = "基线方案有 <b>" + s.hazard_span_km + " km</b> 处于高风险积水段，"
        + "最省的选择是 <b>" + (ch ? ch.label : "—") + "</b>。";
    }

    var kv = $("vKv"), rows = [];
    function row(k, v, cls) {
      rows.push("<dt>" + k + "</dt><dd class=\"" + (cls || "") + "\">" + v + "</dd>");
    }
    row("干线里程", s.total_km + " km" + (s.detour_km > 0 ? "（+绕行 " + s.detour_km + " km）" : ""), "big");
    row("总耗时", Math.round(s.minutes / 60) + " h " + Math.round(s.minutes % 60) + " min");
    row("全程能耗", s.energy_kwh + " kWh");
    row("电量告警点", "K" + s.warn_km + " · " + fmtHM(s.warn_hour));
    row("最低电量", (s.soc_min <= 0 ? "耗尽（未补能）" : Math.round(s.soc_min * 100) + "%")
        + (s.soc_min <= 0.15 ? " ⚠" : ""), s.soc_min <= 0.15 ? "red" : "grn");
    row("高风险段", s.hazard_count ? s.hazard_count + " 段 / " + s.hazard_span_km + " km" : "无");
    row("峰值降水", s.hazard_peak_rain ? s.hazard_peak_rain + " mm/h" : "—");
    row("风险暴露", s.risk_exposure_km + " km·r" + (S.res.base.risk_exposure_km > s.risk_exposure_km
      ? "（原 " + S.res.base.risk_exposure_km + "）" : ""), "grn");
    kv.innerHTML = rows.join("");
  }

  function renderCandidates() {
    var host = $("cands");
    host.innerHTML = "";
    if (!S.res.candidates.length) {
      host.innerHTML = '<div class="deny">没有任何可行方案 —— 这正是模型该说「不」的时候。</div>';
    }
    S.res.candidates.forEach(function (c) {
      var d = document.createElement("div");
      d.className = "cand" + (c.id === S.view ? " on" : "");
      d.onclick = function () { selectCandidate(c.id); };
      var st = c.stops[0];
      d.innerHTML =
        "<div class=\"head\"><span class=\"tagid\">" + c.id + "</span>" +
        "<span class=\"name\">" + c.label + "</span>" +
        (c.chosen ? "<span class=\"pick\">← 模型采用</span>" : "") + "</div>" +
        "<div class=\"nums\">" +
        "<span>里程 <b>" + c.total_km + "</b></span>" +
        "<span>耗时 <b>" + Math.round(c.minutes) + "</b> min</span>" +
        "<span>能耗 <b>" + c.energy_kwh + "</b> kWh</span>" +
        "<span>风险 <b>" + c.risk_exposure_km + "</b></span>" +
        "<span>综合代价 <b>" + c.score + "</b></span>" +
        "</div>" +
        (st ? "<div class=\"nums\" style=\"margin-top:5px\"><span>停靠 <b>" + st.name +
             "</b>（充 " + st.need_kwh + " kWh / " + Math.round(st.dwell_min) + " min）</span></div>" : "");
      host.appendChild(d);
    });
  }

  function riskClass(r) { return r >= 0.5 ? "hi" : r >= 0.25 ? "mid" : "lo"; }

  function renderStations() {
    var host = $("stations");
    var warnKm = S.res.stations.warn_km;
    var list = S.res.stations.ranked.slice().sort(function (a, b) { return a.km - b.km; });
    host.innerHTML = "";
    list.forEach(function (s) {
      var d = document.createElement("div");
      d.className = "st";
      var state = s.behind ? "已越过" : (s.feasible ? "可用" : "不可达");
      d.innerHTML =
        "<span class=\"dot\" style=\"width:9px;height:9px;border-radius:50%;background:" +
        (s.feasible ? C.go : C.off) + "\"></span>" +
        "<span class=\"n\">" + s.name + "<small>K" + s.km + " · " + s.power_kw + " kW · "
        + s.price_yuan_kwh.toFixed(2) + " 元/kWh" + (s.detour_km ? " · 站内绕行 " + s.detour_km + " km" : "")
        + "</small></span>" +
        "<span class=\"risk " + riskClass(s.risk_there) + "\">风险 " + s.risk_there.toFixed(2) + "</span>" +
        "<span class=\"deny\">" + state + "</span>";
      host.appendChild(d);
    });
    $("stNote").innerHTML = "电量告警点 K" + S.res.stations.warn_km + "（" + fmtHM(S.res.stations.warn_hour)
      + "）。「已越过」= 车已开过该站，不可能回头补能 —— 这是最容易做错的地方。"
      + "<br>补给站为依公开资料整理的示意数据（<code>source: "
      + S.data.stationsMeta.source + "</code>），上线应替换为地点检索的真实 POI。";
  }

  function renderWhy() {
    var b = S.res.base, ch = S.res.decision, s = S.res.summary;
    var h = b.hazard[0];
    var out = [];

    if (h) {
      var i0 = Math.max(0, h.i0 - 1), i1 = Math.min(b.km.length - 1, h.i1);
      var iPeak = h.i1;
      var peakLow = 0;
      for (var q = h.i0; q <= h.i1; q++) peakLow = Math.max(peakLow, b.lowland[q]);
      out.push("<b>① 气象 → 风险</b>　" + fmtHM(b.time[i0]) + "–" + fmtHM(b.time[i1])
        + " 经过 K" + s.hazard_km0 + "–K" + s.hazard_km1 + "（约 " + s.hazard_span_km
        + " km），该段降水峰值 <span class=\"hl\">" + h.peak_rain + " mm/h</span>；"
        + "叠加河谷低洼地形系数 " + peakLow.toFixed(2) + "，积水风险达 " + h.peak_risk.toFixed(2)
        + "（阈值 0.50）。");
    } else {
      out.push("<b>① 气象 → 风险</b>　本次行程沿途降水不足以在低洼段形成高风险积水（峰值 "
        + Math.max.apply(null, b.rain).toFixed(1) + " mm/h）。");
    }

    var e = b.breakdown;
    var maxItem = [['滚阻', e.roll], ['爬坡净耗', e.grade], ['风阻', e.drag], ['空调', e.hvac], ['雨阻', e.rain]]
      .sort(function (x, y) { return y[1] - x[1]; })[0];
    out.push("<b>② 能耗</b>　全程 " + s.energy_kwh + " kWh ＝ 爬坡/回收 " + e.grade
      + " ＋ 滚阻 " + e.roll + " ＋ 风阻 " + e.drag + " ＋ 空调 " + e.hvac + " ＋ 雨阻 " + e.rain
      + " kWh。最大单项是" + maxItem[0] + "（" + maxItem[1] + " kWh）；秦岭段爬升 1379 m 的势能"
      + "由下坡按 60% 效率回收抵消了一部分，所以坡项净值不大。");
    out.push("<b>③ 电量</b>　" + fmtHM(b.time[0]) + " 以 " + Math.round(S.res.soc0 * 100)
      + "% 电量出发，K" + s.warn_km + " 触及 30% 告警线。");

    if (ch) {
      var alt = S.res.candidates.filter(function (x) { return x.id === 'A'; })[0] ||
                S.res.candidates.filter(function (x) { return x.id !== ch.id; })[0];
      var dKm = alt ? (ch.total_km - alt.total_km) : ch.detour_km;
      var dMin = alt ? (ch.minutes - alt.minutes) : 0;
      out.push("<b>④ 选址与绕行</b>　采用 <b>" + ch.label + "</b>：风险暴露 <span class=\"hl\">"
        + S.res.base.risk_exposure_km + " → " + ch.risk_exposure_km + " km·r</span>"
        + (alt ? "；相对「" + alt.label + "」多走 " + dKm.toFixed(1) + " km、多花 "
                 + Math.round(dMin) + " 分钟" : "")
        + "。补能站选在 <b>" + (ch.stops[0] ? ch.stops[0].name : "—") + "</b>（K"
        + (ch.stops[0] ? ch.stops[0].km : 0) + "，到站电量 "
        + (ch.stops[0] ? Math.round(ch.stops[0].soc_on_arrival * 100) + "%" : "—") + "）。");
    }
    out.push('<span class="note">雨带是「空间高斯 × 时间高斯」，所以早出发会改变结论 —— 切到 04:00 情景对比看看。</span>');
    $("why").innerHTML = out.join("<br>");
  }

  /* ────────────────────────── 地图 ────────────────────────── */
  function initMap() {
    if (!window.APP_CONFIG.ak) {
      $("mapTag").textContent = "离线示意底图（未配置 AK）· 干线 / 雨带 / 积水段 / 补给站";
      return;
    }
    if (window.__bmapFailed) { $("mapTag").textContent = "百度底图加载失败，已切到离线示意底图"; return; }
    window.onBMapReady(function () { setupGL(); });
    setTimeout(function () {
      if (!S.useGL) $("mapTag").textContent = "百度底图超时，已切到离线示意底图";
    }, 6000);
  }

  function setupGL() {
    try {
      S.map = new BMapGL.Map("bmap");
      S.map.centerAndZoom(new BMapGL.Point(APP_CONFIG.center[0], APP_CONFIG.center[1]), APP_CONFIG.zoom);
      S.map.enableScrollWheelZoom(true);
      S.useGL = true;
      var fb = $("fallback"); if (fb) fb.style.display = "none";
      $("mapTag").textContent = "底图：百度地图 JSAPI GL ｜ 数据：离线缓存（可复现）";
      // 用干线自身的包围盒定视口，比写死 zoom 稳
      try {
        S.map.setViewport(S.data.corridor.polyline.map(function (p) {
          return new BMapGL.Point(p[0], p[1]);
        }), { margins: [70, 70, 70, 70] });
      } catch (e) { /* 旧版无 setViewport 时忽略 */ }
      renderMap();
    } catch (e) {
      $("mapTag").textContent = "JSAPI 初始化失败，已切到离线示意底图";
    }
  }

  function renderMap() {
    if (S.useGL && S.map) renderGL(); else renderCanvas();
  }

  function renderGL() {
    var map = S.map;
    map.clearOverlays();
    var pts = function (arr) {
      return arr.map(function (p) { return new BMapGL.Point(p[0], p[1]); });
    };
    // 原路线
    map.addOverlay(new BMapGL.Polyline(pts(S.data.corridor.polyline),
      { strokeColor: C.line, strokeWeight: 4, strokeOpacity: .85 }));
    // 高风险段（加粗红线）——注意要用「沿线密集顶点」而不是 29 个采样点
    S.res.base.hazard.forEach(function (h) {
      var seg = hazardBd(h);
      if (seg.length > 1) {
        map.addOverlay(new BMapGL.Polyline(pts(seg),
          { strokeColor: C.danger, strokeWeight: 10, strokeOpacity: .35 }));
        map.addOverlay(new BMapGL.Polyline(pts(seg),
          { strokeColor: C.danger, strokeWeight: 5, strokeOpacity: .95 }));
      }
    });
    // 选中方案路线
    var ch = S.res.decision;
    if (ch && ch.id === "B") {
      map.addOverlay(new BMapGL.Polyline(pts(ch.polyline),
        { strokeColor: C.lineHot, strokeWeight: 5, strokeOpacity: .95 }));
    }
    // 补给站
    S.res.stations.ranked.forEach(function (s) {
      var st = S.data.stations.filter(function (x) { return x.id === s.id; })[0];
      if (!st) return;
      var p = nearestPoint(st.km);
      map.addOverlay(new BMapGL.Circle(new BMapGL.Point(p[0], p[1]), 9000, {
        strokeColor: s.feasible ? C.go : C.off, strokeWeight: 2,
        fillColor: s.feasible ? C.go : C.off, fillOpacity: .75
      }));
    });
    // 告警点
    var wp = nearestPoint(S.res.stations.warn_km);
    if (wp) {
      map.addOverlay(new BMapGL.Circle(new BMapGL.Point(wp[0], wp[1]), 12000, {
        strokeColor: C.warn, strokeWeight: 3, fillColor: C.warn, fillOpacity: .8
      }));
    }
  }

  /* 取「沿线密集顶点」中处于高风险区间的那一段（供地图叠加用） */
  function hazardBd(h) {
    var poly = S.data.corridor.polyline, cum = S.baseCum, out = [];
    for (var i = 0; i < poly.length; i++) {
      if (cum[i] >= h.km0 - 14 && cum[i] <= h.km1 + 14) out.push(poly[i]);
    }
    return out;
  }

  function nearestPoint(km) {
    var best = null, bd = Infinity;
    S.data.corridor.points.forEach(function (p) {
      var d = Math.abs(p.km - km);
      if (d < bd) { bd = d; best = p.bd; }
    });
    return best;
  }

  /* ── 离线示意底图：不依赖网络，保证演示与录屏可复现 ── */
  function renderCanvas() {
    var cv = $("fallback");
    var box = cv.parentNode.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    cv.width = Math.max(320, box.width) * dpr;
    cv.height = Math.max(240, box.height) * dpr;
    var g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    var W = cv.width / dpr, H = cv.height / dpr;

    g.clearRect(0, 0, W, H);
    var bg = g.createLinearGradient(0, 0, 0, H);
    bg.addColorStop(0, "#f7fafd"); bg.addColorStop(1, "#e9f0f7");
    g.fillStyle = bg; g.fillRect(0, 0, W, H);

    // 投影
    var all = S.data.corridor.polyline.slice();
    if (S.res.decision && S.res.decision.polyline) all = all.concat(S.res.decision.polyline);
    S.res.stations.ranked.forEach(function (s) {
      var p = nearestPoint(s.km); if (p) all.push(p);
    });
    var xs = all.map(function (p) { return p[0]; }), ys = all.map(function (p) { return p[1]; });
    var minx = Math.min.apply(null, xs), maxx = Math.max.apply(null, xs);
    var miny = Math.min.apply(null, ys), maxy = Math.max.apply(null, ys);
    var pad = 46;
    var sx = (W - pad * 2) / Math.max(1e-6, maxx - minx);
    var sy = (H - pad * 2) / Math.max(1e-6, maxy - miny);
    var k = Math.min(sx, sy);
    var ox = pad + (W - pad * 2 - (maxx - minx) * k) / 2;
    var oy = pad + (H - pad * 2 - (maxy - miny) * k) / 2;
    function P(p) { return [ox + (p[0] - minx) * k, H - oy - (p[1] - miny) * k]; }

    // 经度/纬度格线
    g.strokeStyle = "#e2eaf2"; g.lineWidth = 1;
    for (var i = 1; i < 6; i++) {
      var x = pad + (W - pad * 2) * i / 6, y = pad + (H - pad * 2) * i / 6;
      g.beginPath(); g.moveTo(x, pad); g.lineTo(x, H - pad); g.stroke();
      g.beginPath(); g.moveTo(pad, y); g.lineTo(W - pad, y); g.stroke();
    }

    // 时间雨带：把当前时刻的降水画在干线上（越深＝雨越大）
    var base = S.res.base_points;
    for (var j = 1; j < base.length; j++) {
      var w1 = M.weatherAt(S.data.weather.grid, base[j - 1].base_km, S.hour);
      var w2 = M.weatherAt(S.data.weather.grid, base[j].base_km, S.hour);
      var rain = ((w1 ? w1.rain_mm_h : 0) + (w2 ? w2.rain_mm_h : 0)) / 2;
      var a = Math.min(0.85, rain / 55);
      if (a <= 0.02) continue;
      var p1 = P(base[j - 1].bd), p2 = P(base[j].bd);
      g.strokeStyle = "rgba(58,160,232," + a.toFixed(3) + ")";
      g.lineWidth = 16; g.lineCap = "round";
      g.beginPath(); g.moveTo(p1[0], p1[1]); g.lineTo(p2[0], p2[1]); g.stroke();
    }

    // 原路线
    drawPath(g, S.data.corridor.polyline, P, C.line, 3.2, .9);

    // 高风险段
    S.res.base.hazard.forEach(function (h) {
      var seg = [];
      for (var q = 0; q < base.length; q++) {
        if (base[q].km >= h.km0 - 14 && base[q].km <= h.km1 + 14) seg.push(base[q].bd);
      }
      if (seg.length > 1) {
        drawPath(g, seg, P, "rgba(217,48,37,.20)", 20, 1);
        drawPath(g, seg, P, C.danger, 4.6, 1);
      }
    });

    // 选中方案
    var ch = S.res.decision;
    if (ch && ch.id === "B") drawPath(g, ch.polyline, P, C.lineHot, 3.6, 1);

    // 补给站
    S.res.stations.ranked.forEach(function (s) {
      var p0 = nearestPoint(s.km); if (!p0) return;
      var q = P(p0);
      g.beginPath();
      g.arc(q[0], q[1], s.feasible ? 6 : 4.5, 0, Math.PI * 2);
      g.fillStyle = s.feasible ? C.go : C.off;
      g.fill();
      g.strokeStyle = "#fff"; g.lineWidth = 1.6; g.stroke();
    });

    // 告警点
    var wp = nearestPoint(S.res.stations.warn_km);
    if (wp) { var q2 = P(wp); ring(g, q2[0], q2[1], 7, C.warn, "⚠"); }

    // 车辆位置
    var t = S.path.trace, idx = -1;
    for (var r = 0; r < t.time.length; r++) if (t.time[r] <= S.hour + 1e-6) idx = r;
    if (idx >= 0 && S.hour >= S.res.depart_hour - 1e-6) {
      var bd = S.path.points[idx].bd;
      var q3 = P(bd);
      g.beginPath(); g.arc(q3[0], q3[1], 8, 0, Math.PI * 2);
      g.fillStyle = "rgba(22,32,43,.16)"; g.fill();
      g.beginPath(); g.arc(q3[0], q3[1], 5.2, 0, Math.PI * 2);
      g.fillStyle = C.ink; g.fill();
      g.strokeStyle = "#fff"; g.lineWidth = 1.6; g.stroke();
    }

    // 端点标注
    label(g, P(S.data.corridor.polyline[0]), "西安", "left");
    label(g, P(S.data.corridor.polyline[S.data.corridor.polyline.length - 1]), "成都", "right");
  }

  function drawPath(g, arr, P, color, w, alpha) {
    if (arr.length < 2) return;
    g.save();
    g.globalAlpha = alpha;
    g.strokeStyle = color; g.lineWidth = w;
    g.lineJoin = "round"; g.lineCap = "round";
    g.beginPath();
    for (var i = 0; i < arr.length; i++) {
      var q = P(arr[i]);
      if (i === 0) g.moveTo(q[0], q[1]); else g.lineTo(q[0], q[1]);
    }
    g.stroke();
    g.restore();
  }

  function ring(g, x, y, r, color, glyph) {
    g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2);
    g.fillStyle = color; g.fill();
    g.strokeStyle = "#fff"; g.lineWidth = 2; g.stroke();
    if (glyph) {
      g.fillStyle = "#fff"; g.font = "bold 9px sans-serif";
      g.textAlign = "center"; g.textBaseline = "middle";
      g.fillText(glyph, x, y + .5);
    }
  }

  function label(g, q, text, align) {
    g.font = "600 12px 'Microsoft YaHei',sans-serif";
    g.textAlign = align === "left" ? "left" : "right";
    g.textBaseline = "middle";
    var dx = align === "left" ? 12 : -12;
    g.fillStyle = "rgba(255,255,255,.9)";
    var w = g.measureText(text).width;
    g.fillRect(q[0] + dx - (align === "left" ? 4 : w + 4), q[1] - 9, w + 8, 18);
    g.fillStyle = C.ink;
    g.fillText(text, q[0] + dx, q[1]);
  }

  /* ────────────────────────── 剖面图 ────────────────────────── */
  function renderProfile() {
    var cv = $("profile");
    var box = cv.parentNode.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    var W = Math.max(360, box.width - 28), H = Math.max(120, box.height - 44);
    cv.width = W * dpr; cv.height = H * dpr;
    cv.style.height = H + "px";
    var g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);

    var L = 46, R = 12, T = 16, B = 20;
    var x0 = L, x1 = W - R, y0 = T, y1 = H - B;
    var b = S.res.base, t = S.path.trace, soc = S.path.soc;
    var kmMax = b.km[b.km.length - 1];
    function X(km) { return x0 + (km / kmMax) * (x1 - x0); }
    function Y(v, lo, hi) { return y1 - (v - lo) / Math.max(1e-6, hi - lo) * (y1 - y0); }

    // 网格
    g.strokeStyle = "#eef2f6"; g.lineWidth = 1;
    for (var i = 0; i <= 4; i++) {
      var y = y0 + (y1 - y0) * i / 4;
      g.beginPath(); g.moveTo(x0, y); g.lineTo(x1, y); g.stroke();
    }
    // 里程刻度
    g.fillStyle = "#8b9aa8"; g.font = "11px sans-serif"; g.textAlign = "center"; g.textBaseline = "top";
    for (var k = 0; k <= 6; k++) {
      var km = kmMax * k / 6;
      g.fillText(Math.round(km) + "km", X(km), y1 + 5);
    }

    // 高风险段底纹
    b.hazard.forEach(function (h) {
      g.fillStyle = "rgba(217,48,37,.09)";
      g.fillRect(X(h.km0), y0, Math.max(2, X(h.km1) - X(h.km0)), y1 - y0);
    });

    // 海拔（灰面）
    var els = S.res.base_points.map(function (p) { return p.elev_m; });
    var eMin = Math.min.apply(null, els), eMax = Math.max.apply(null, els);
    g.beginPath();
    g.moveTo(X(b.km[0]), Y(els[0], eMin, eMax));
    for (var m = 1; m < b.km.length; m++) g.lineTo(X(b.km[m]), Y(els[m], eMin, eMax));
    g.lineTo(X(kmMax), y1); g.lineTo(X(0), y1); g.closePath();
    g.fillStyle = "rgba(185,196,206,.42)"; g.fill();
    g.strokeStyle = C.elev; g.lineWidth = 1.4; g.stroke();

    // 降水（蓝面，归一到 0..60mm/h）
    g.beginPath();
    g.moveTo(X(b.km[0]), y1);
    for (var m2 = 0; m2 < b.km.length; m2++) g.lineTo(X(b.km[m2]), Y(Math.min(60, b.rain[m2]), 0, 60));
    g.lineTo(X(kmMax), y1); g.closePath();
    g.fillStyle = "rgba(58,160,232,.30)"; g.fill();

    // 风险（红面 0..1）
    g.beginPath();
    g.moveTo(X(b.km[0]), y1);
    for (var m3 = 0; m3 < b.km.length; m3++) g.lineTo(X(b.km[m3]), Y(b.risk[m3], 0, 1));
    g.lineTo(X(kmMax), y1); g.closePath();
    g.fillStyle = "rgba(217,48,37,.34)"; g.fill();

    // SOC（绿线 0..1）
    g.strokeStyle = C.go; g.lineWidth = 2.4; g.beginPath();
    for (var m4 = 0; m4 < t.km.length; m4++) {
      var px = X(t.km[m4]), py = Y(Math.max(0, soc[m4]), 0, 1);
      if (m4 === 0) g.moveTo(px, py); else g.lineTo(px, py);
    }
    g.stroke();
    // 30% 告警线
    g.setLineDash([5, 4]); g.strokeStyle = "rgba(232,163,61,.9)"; g.lineWidth = 1.4;
    g.beginPath(); g.moveTo(x0, Y(0.30, 0, 1)); g.lineTo(x1, Y(0.30, 0, 1)); g.stroke();
    g.setLineDash([]);
    g.fillStyle = "#a9701a"; g.font = "11px sans-serif"; g.textAlign = "right"; g.textBaseline = "middle";
    g.fillText("30% 告警", x1 - 4, Y(0.30, 0, 1) - 9);

    // 补给站刻度
    g.textAlign = "center"; g.textBaseline = "bottom";
    S.res.stations.ranked.forEach(function (s) {
      if (!s.feasible) return;
      var x = X(s.km);
      g.strokeStyle = "rgba(15,157,88,.55)"; g.lineWidth = 1;
      g.beginPath(); g.moveTo(x, y0); g.lineTo(x, y1); g.stroke();
      g.fillStyle = C.go; g.font = "10px sans-serif";
      g.fillText(s.name.replace(/服务区.*|·.*/g, ""), x, y0 - 3);
    });

    // 当前时刻游标
    var idx = -1;
    for (var r = 0; r < t.time.length; r++) if (t.time[r] <= S.hour + 1e-6) idx = r;
    if (idx >= 0 && S.hour >= S.res.depart_hour - 1e-6) {
      var cx = X(t.km[idx]);
      g.strokeStyle = "rgba(22,32,43,.55)"; g.lineWidth = 1.6;
      g.beginPath(); g.moveTo(cx, y0); g.lineTo(cx, y1); g.stroke();
      g.fillStyle = C.ink; g.font = "600 11px sans-serif"; g.textAlign = "center"; g.textBaseline = "top";
      g.fillText(fmtHM(S.hour) + " · K" + Math.round(t.km[idx]), cx, y0);
    }

    // 纵轴说明
    g.fillStyle = "#8b9aa8"; g.font = "11px sans-serif"; g.textAlign = "right"; g.textBaseline = "middle";
    g.fillText("雨 60", x0 - 6, Y(60, 0, 60));
    g.fillText("雨 0", x0 - 6, y1);
    g.fillText("SOC 100%", x0 - 6, y0 + 2);
  }

  function togglePlay() {
    if (S.timer) {
      clearInterval(S.timer); S.timer = null;
      $("playBtn").textContent = "▶ 播放";
      return;
    }
    $("playBtn").textContent = "❚❚ 暂停";
    S.timer = setInterval(function () {
      S.hour += 0.15;
      if (S.hour > 24) { S.hour = 0; }
      $("time").value = S.hour;
      paintTime(); renderMap(); renderProfile();
    }, 60);
  }

  window.addEventListener("resize", function () {
    renderMap(); renderProfile();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
