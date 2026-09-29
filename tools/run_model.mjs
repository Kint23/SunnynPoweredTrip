/* 跑模型 + 打印结论（Node CLI）。也是"金标"的来源。
 *
 * 用法：
 *   node tools/run_model.mjs                 # 打印三个情景的结论
 *   node tools/run_model.mjs --scenario s1-0800
 *   node tools/run_model.mjs --update-expect # 把当前结论写回 data/scenarios.json 的 expect 字段
 *
 * 为什么用 Node 跑同一份模型：模型只有一份（app/model.js），
 *   浏览器和校验脚本共用，避免"文档里的数字和页面对不上"。
 */
import { createRequire } from 'node:module';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const require = createRequire(import.meta.url);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const M = require(join(ROOT, 'app', 'model.js'));

const read = (p) => JSON.parse(readFileSync(join(ROOT, 'data', p), 'utf8'));

const corridors = read('corridors.json');
const weather = read('weather_cache.json');
const terrain = read('terrain.json');
const stationsFile = read('stations.json');
const scenariosFile = read('scenarios.json');

const corridor = corridors.corridors[0];

if (scenariosFile.corridor_id !== corridor.id) {
  console.error(`✗ scenarios.corridor_id=${scenariosFile.corridor_id} 与 corridors.id=${corridor.id} 不一致`);
  process.exit(2);
}

export function runOne(sc) {
  const res = M.simulate({
    corridor, weather, terrain, stations: stationsFile.stations,
    depart_hour: sc.depart_hour, soc0: sc.soc0, mass_kg: sc.mass_kg
  });
  return res;
}

function expectOf(res) {
  const s = res.summary;
  return {
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
}

const args = process.argv.slice(2);
const only = args.includes('--scenario') ? args[args.indexOf('--scenario') + 1] : null;
const update = args.includes('--update-expect');

let changed = false;
for (const sc of scenariosFile.scenarios) {
  const res = runOne(sc);
  const exp = expectOf(res);
  if (only && sc.id !== only) continue;

  console.log(`\n=== ${sc.id} · ${sc.title} ===`);
  console.log(`  出发 ${sc.depart_hour}:00 ｜ SOC₀ ${(sc.soc0 * 100).toFixed(0)}% ｜ 总重 ${(sc.mass_kg / 1000).toFixed(1)} t`);
  console.log(`  里程 ${exp.total_km} km ｜ 在途 ${Math.round(res.base.minutes)} min ｜ 能耗 ${exp.energy_kwh} kWh`);
  console.log(`  高风险段 ${exp.hazard_count} 段 ｜ 峰值降水 ${exp.hazard_peak_rain} mm/h ｜ 基线风险暴露 ${res.base.risk_exposure_km} km·r`);
  console.log(`  电量告警 @ ${exp.warn_km} km（${res.stations.warn_hour.toFixed(1)} 时）｜ 最低 SOC ${exp.soc_min_pct}% ｜ 终点 SOC ${exp.soc_end_pct}%`);
  console.log(`  判定：${exp.verdict}` + (exp.charge_stops ? ` ｜ 补能 ${exp.charge_stops} 次` : ''));
  console.log(`  能耗构成：坡 ${res.base.breakdown.grade} / 滚阻 ${res.base.breakdown.roll} / 风阻 ${res.base.breakdown.drag} / 空调 ${res.base.breakdown.hvac} / 雨阻 ${res.base.breakdown.rain} kWh`);
  for (const c of res.candidates) {
    console.log(`   [${c.id}${c.chosen ? '★' : ' '}] ${c.label} ｜ ${c.total_km} km ｜ ${Math.round(c.minutes)} min ｜ ` +
      `${c.energy_kwh} kWh ｜ 风险暴露 ${c.risk_exposure_km} ｜ 折算 ${c.score}` +
      (c.stops.length ? ` ｜ 停 ${c.stops[0].name}` : ''));
  }
  const top = res.stations.ranked.filter(s => s.feasible).slice(0, 3);
  if (!top.length) console.log('      （无可行补给站）');
  for (const s of top) {
    console.log(`      候选站 ${s.name} ｜ 到站 SOC ${(s.soc_on_arrival * 100).toFixed(0)}% ｜ ` +
      `站内绕行 ${s.detour_km} km ｜ 到站时刻风险 ${s.risk_there} ｜ 充 ${s.need_kwh} kWh / ${Math.round(s.dwell_min)} min`);
  }

  if (update && JSON.stringify(sc.expect) !== JSON.stringify(exp)) {
    sc.expect = exp;
    changed = true;
  }
}

if (update && changed) {
  writeFileSync(join(ROOT, 'data', 'scenarios.json'),
    JSON.stringify(scenariosFile, null, 1), 'utf8');
  console.log('\n✓ 已把金标写回 data/scenarios.json');
}
