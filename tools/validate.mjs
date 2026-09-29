/* 数据契约 + 金标自检（Node CLI）。
 *
 * 为什么要有这一步（照搬 ohbj 的成功经验）：
 *   结论数字必须**可复现**。模型只有一份（app/model.js），
 *   data/scenarios.json 里冻结着每个情景的"金标"，本脚本重算一遍并逐项比对；
 *   任何一行改动让结论漂了，这里立刻红。
 *
 * 用法：
 *   node tools/validate.mjs
 *   node tools/validate.mjs --update    # 接受当前结果为新金标（改动模型时先看 diff）
 */
import { createRequire } from 'node:module';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const require = createRequire(import.meta.url);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const M = require(join(ROOT, 'app', 'model.js'));

const read = (p) => JSON.parse(readFileSync(join(ROOT, 'data', p), 'utf8'));

let pass = 0, fail = 0;
const ok = (msg) => { pass++; console.log(`  ✓ ${msg}`); };
const bad = (msg) => { fail++; console.log(`  ✗ ${msg}`); };
function eq(name, got, want, tol) {
  const d = Math.abs(got - want);
  if (d <= tol) ok(`${name} = ${got}（金标 ${want}，差 ${d.toFixed(3)}）`);
  else bad(`${name} = ${got}，金标 ${want}，差 ${d.toFixed(3)} > 容差 ${tol}`);
}

const corridors = read('corridors.json');
const weather = read('weather_cache.json');
const terrain = read('terrain.json');
const stationsFile = read('stations.json');
const scenFile = read('scenarios.json');
const corridor = corridors.corridors[0];

console.log('\n[1] 数据契约');
{
  const need = ['id', 'name', 'crs', 'total_km', 'polyline', 'anchors', 'points', 'vehicle'];
  const miss = need.filter((k) => !(k in corridor));
  miss.length ? bad(`corridors 缺字段：${miss.join(', ')}`) : ok('corridors 字段完整');

  corridor.crs === 'BD-09' ? ok('坐标系标注为 BD-09（百度地图用）') : bad(`crs = ${corridor.crs}`);

  const ks = corridor.points.map((p) => p.km);
  const mono = ks.every((v, i) => i === 0 || v > ks[i - 1]);
  mono ? ok(`采样点里程单调递增（${ks.length} 点，0→${ks[ks.length - 1]} km）`) : bad('采样点里程非单调');

  const inChina = corridor.polyline.every((p) => p[0] > 73 && p[0] < 136 && p[1] > 3 && p[1] < 54);
  inChina ? ok(`干线 ${corridor.polyline.length} 个顶点均落在中国境内`) : bad('存在超出中国范围的坐标');

  const hv = corridor.polyline.some((p) => Math.abs(p[0]) > 180 || Math.abs(p[1]) > 90);
  hv ? bad('坐标疑似未换算（出现超出经纬度范围的值）') : ok('坐标数量级正常（已换算到 BD-09）');

  const wg = weather.grid;
  wg.length === corridor.points.length
    ? ok(`气象网格与采样点一一对应（${wg.length} 点）`)
    : bad(`气象网格 ${wg.length} ≠ 采样点 ${corridor.points.length}`);
  const hrs = wg[0].hours.length;
  hrs === 24 ? ok('逐点均含 24 小时逐小时预报') : bad(`逐小时条数 = ${hrs}`);
  const rmax = Math.max(...wg.flatMap((g) => g.hours.map((h) => h.rain_mm_h)));
  ok(`全程最大降水 ${rmax.toFixed(1)} mm/h（${rmax > 30 ? '达到暴雨量级' : '未达暴雨'}）`);

  (terrain.lowland_bands || []).length
    ? ok(`低洼带 ${terrain.lowland_bands.length} 段，横向衰减半径 ${terrain.lowland_falloff_km} km`)
    : bad('terrain 缺少 lowland_bands（积水风险将无法形成）');

  stationsFile.stations.every((s) => s.source)
    ? ok(`补给站 ${stationsFile.stations.length} 个，均标注 source（诚实标注来源）`)
    : bad('补给站缺少 source 字段');
}

console.log('\n[2] 几何性质：绕行必须真的"横向"推开');
{
  const poly = corridor.polyline;
  const cum = M.cumulativeKm(poly);
  const hz = M.simulate({
    corridor, weather, terrain, stations: stationsFile.stations,
    depart_hour: scenFile.scenarios[0].depart_hour,
    soc0: scenFile.scenarios[0].soc0, mass_kg: scenFile.scenarios[0].mass_kg
  }).base.hazard[0];

  if (!hz) {
    bad('主情景未产生高风险段，无法校验绕行几何');
  } else {
    const polyB = M.detourPolyline(poly, cum, hz.km0 - 10, hz.km1 + 10, 6, 6);
    const cumB = M.cumulativeKm(polyB);
    let lat = [];
    for (let i = 0; i < polyB.length; i++) {
      if (cum[i] > hz.km0 && cum[i] < hz.km1) lat.push(M.projectToBase(poly, cum, polyB[i]).lateral_km);
    }
    const minLat = Math.min(...lat), maxLat = Math.max(...lat);
    // 曾经踩过的坑：法向量算错会得到"部分沿航向"的位移，横向距离会塌成 0
    (minLat > 3.0 && maxLat < 7.5)
      ? ok(`绕行段横向偏移 ${minLat.toFixed(2)}–${maxLat.toFixed(2)} km（目标 6 km）`)
      : bad(`绕行段横向偏移 ${minLat.toFixed(2)}–${maxLat.toFixed(2)} km，偏离目标 6 km 太多`);

    const extra = M.cumulativeKm(polyB).slice(-1)[0] - cum.slice(-1)[0];
    (extra > 0 && extra < 25) ? ok(`绕行附加里程 ${extra.toFixed(2)} km（合理区间 0–25）`)
      : bad(`绕行附加里程 ${extra.toFixed(2)} km 不合理`);
  }
}

console.log('\n[3] 逻辑不变量');
{
  const res = M.simulate({
    corridor, weather, terrain, stations: stationsFile.stations,
    depart_hour: scenFile.scenarios[0].depart_hour,
    soc0: scenFile.scenarios[0].soc0, mass_kg: scenFile.scenarios[0].mass_kg
  });
  const behindOk = res.stations.ranked.every((s) => !(s.behind && s.feasible));
  behindOk ? ok('已越过的补给站不会被判为可用') : bad('存在"已越过却可用"的补给站');

  const dt = res.candidates.map((c) => c.detour_km);
  (dt.length === 0 || Math.min(...dt) >= 0) ? ok('候选方案绕行里程非负') : bad('出现负的绕行里程');

  const socOk = res.candidates.every((c) => c.soc.length === c.km.length);
  socOk ? ok('候选方案的 SOC 曲线与里程序列等长（剖面图可画）') : bad('SOC 曲线长度不匹配');

  // 「声称等待」必须真的在时间轴上等 —— 曾经踩过：pause 参数没接上，
  // 候选 C 标着"等待 60 分钟"，耗时却和不等待一模一样。
  const waitOk = res.candidates.every((c) => {
    if (!c.wait_min) return true;
    return c.minutes - res.base.minutes >= c.wait_min * 0.9;
  });
  waitOk ? ok('所有"等待"候选的耗时都真的包含了等待时长')
    : bad('存在声明了等待、耗时却没有相应增加的候选');

  if (res.decision) {
    const best = res.candidates[0];
    const allLower = res.candidates.every((c) => c.score >= best.score - 1e-9);
    allLower ? ok(`采用方案 ${best.id} 的综合代价最低（${best.score}）`) : bad('采用的不是代价最低方案');

    const reducesRisk = res.candidates.every((c) => c.risk_exposure_km <= res.base.risk_exposure_km + 1e-6);
    reducesRisk ? ok('所有候选方案的风险暴露都不高于基线') : bad('存在比基线风险更高的候选');
  } else {
    ok('本情景无可行方案（判定为不可行，属预期）');
  }
}

console.log('\n[4] 金标复算');
let changed = false;
for (const sc of scenFile.scenarios) {
  const res = M.simulate({
    corridor, weather, terrain, stations: stationsFile.stations,
    depart_hour: sc.depart_hour, soc0: sc.soc0, mass_kg: sc.mass_kg
  });
  const s = res.summary;
  const got = {
    total_km: s.total_km,
    hazard_count: s.hazard_count,
    hazard_peak_rain: Math.round(s.hazard_peak_rain * 10) / 10,
    warn_km: s.warn_km,
    soc_min_pct: Math.round(s.soc_min * 1000) / 10,
    soc_end_pct: Math.round(s.soc_end * 1000) / 10,
    risk_exposure_km: Math.round(s.risk_exposure_km * 100) / 100,
    charge_stops: s.charge_stops,
    detour_km: s.detour_km,
    minutes: s.minutes,
    energy_kwh: s.energy_kwh,
    verdict: s.verdict,
    chosen: res.chosen_id
  };
  console.log(`  ── ${sc.id}（${sc.title}）`);
  if (!sc.expect || !Object.keys(sc.expect).length) { bad(`${sc.id} 缺少金标`); continue; }
  eq(`${sc.id}.total_km`, got.total_km, sc.expect.total_km, 0.05);
  eq(`${sc.id}.warn_km`, got.warn_km, sc.expect.warn_km, 0.5);
  eq(`${sc.id}.soc_min_pct`, got.soc_min_pct, sc.expect.soc_min_pct, 0.5);
  eq(`${sc.id}.risk_exposure_km`, got.risk_exposure_km, sc.expect.risk_exposure_km, 0.1);
  eq(`${sc.id}.minutes`, got.minutes, sc.expect.minutes, 1.0);
  eq(`${sc.id}.energy_kwh`, got.energy_kwh, sc.expect.energy_kwh, 1.0);
  if (got.verdict !== sc.expect.verdict) bad(`${sc.id}.verdict = ${got.verdict}，金标 ${sc.expect.verdict}`);
  else ok(`${sc.id}.verdict = ${got.verdict}`);
  if (String(got.chosen) !== String(sc.expect.chosen)) bad(`${sc.id}.chosen = ${got.chosen}，金标 ${sc.expect.chosen}`);
  else ok(`${sc.id}.chosen = ${got.chosen}`);
  if (JSON.stringify(sc.expect) !== JSON.stringify(got)) { sc.expect = got; changed = true; }
}

if (process.argv.includes('--update') && changed) {
  writeFileSync(join(ROOT, 'data', 'scenarios.json'), JSON.stringify(scenFile, null, 1), 'utf8');
  console.log('\n✓ 已接受当前结果为新的金标（data/scenarios.json）');
}

console.log(`\n${fail === 0 ? '全部通过' : '存在失败项'}：${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
