# 晴好出发 · 基于气象与能耗预测的物流调度

把**预测**接进算路闭环：沿路线取点拉取逐小时气象 → 圈出低洼高风险积水段 →
按坡度/风阻/暴雨/空调加权算百公里能耗 → 定位电量枯竭点 → 就近匹配补给站 →
一次性给出「避雨 + 补能」的**可执行路线**与代价对比。

参赛赛道：百度地图开放平台 · 开发者创作大赛 · **智能物流**

---

## 作品演示

[![点击播放正片](banner/teaser.webp)](https://kint23.github.io/SunnynPoweredTrip/video/demo.mp4)

🖥 **在线 Demo**：<https://kint23.github.io/SunnynPoweredTrip/>
📷 **封面图**：`banner/晴好出发-banner-1200x675.png`

> ⚠️ GitHub 的 README 会过滤 `<video>` / `<iframe>`，所以用**可点击的动图**作播放入口。

---

## 一、快速开始

```powershell
uv run python -m http.server 8080
# 打开 http://localhost:8080/
```

> 直接双击 `index.html` 会因浏览器 CORS 限制读不到 `data/*.json`。

**配百度地图 AK**：到 <https://lbsyun.baidu.com/apiconsole/key> 建一个**浏览器端** AK，
把 `localhost` 与线上域名填进 Referer 白名单，写进 `app/config.js` 的 `ak`。

> `ak` 留空时页面**依然完整可用**：自动切到自绘的「离线示意底图」（干线 / 雨带 / 积水段 / 补给站，
> 全部数据驱动绘制）。这条降级路径是**故意保留**的 —— 它让演示与录屏**不依赖网络与配额**，永远可复现。
> **服务端 AK 千万不要写进 `app/config.js`，也不要提交到仓库。**

---

## 二、数据流水线（可复现）

```
tools/make_demo_data.py          ① 生成离线缓存（确定性、不联网、不消耗配额）
        │
        ├─ data/corridors.json     干线几何 + 采样点（BD-09）+ 车辆参数
        ├─ data/weather_cache.json 逐点 24 小时逐小时预报
        ├─ data/terrain.json       自建坡度字典 + 低洼带（模型假设，非实测）
        ├─ data/stations.json      补给站（示意数据，source: curated）
        └─ data/scenarios.json     三个演示情景 + expect 金标
                │
app/model.js  ─┤  ② 预测与优化（纯函数，浏览器 / Node 双用）
        │      │
        │      └─ tools/run_model.mjs --update-expect   把当前结论冻结为金标
        │
        └───────────►  tools/validate.mjs   ③ 自检：契约 + 几何性质 + 逻辑不变量 + 金标复算
```

| 步骤 | 命令 |
|---|---|
| ① 生成数据 | `uv run tools/make_demo_data.py` |
| ② 跑模型 / 冻结金标 | `node tools/run_model.mjs` ｜ `node tools/run_model.mjs --update-expect` |
| ③ 自检（41 项） | `node tools/validate.mjs` |
| ④ Banner | `uv run tools/make_banner.py` |
| ⑤ 正片 | `uv run tools/record_demo.py` |

> **纪律**：页面**不直接调 WebAPI**，只读 `data/*.json`。
> 原因见 `docs/方案与计划.md` §0.3 —— 地点检索未认证仅 100 次/日，
> 且演示视频必须可复现，缓存是唯一同时满足这两点的做法。
> 接真实数据时，把 `tools/fetch_weather.py` / `fetch_stations.py` 的产物写进同名文件即可，算法与前端一行都不用改。

---

## 三、目录结构

```
index.html                  单页 Demo 入口
app/
  config.js                 AK 配置 + JSAPI 加载握手（AK 留空则走离线示意底图）
  model.js                  ★ 预测与优化模型（纯函数，浏览器/Node 双用）
  app.js                    地图、时间轴、剖面图、对比卡、可解释文本
  style.css                 气象色系样式
data/                       ★ 前端唯一数据源（离线缓存）
  corridors.json            干线 + 采样点 + 车辆参数
  weather_cache.json        逐点逐小时预报
  terrain.json              坡度字典 + 低洼带（低洼是积水风险的"闸门"）
  stations.json             补给站
  scenarios.json            情景 + 金标（expect）
tools/
  make_demo_data.py         ① 离线生成演示数据
  run_model.mjs             ② 跑模型 / 冻结金标
  validate.mjs              ③ 自检（含绕行几何的回归测试）
  make_banner.py            ④ Banner（文字与地图都由数据绘制）
  record_demo.py            ⑤ 60 秒正片（CDP 逐帧采集 → 烧字幕 → 混音）
  make_music.py             自制配乐（无版权顾虑，正片默认用它）
  make_subs.py             ↕ 独立的 SRT→ASS 小工具（record_demo 内部已自带写 ASS）
  comfy_generate.py        ↕ 可选的 ComfyUI 出图（想给 Banner 加 AI 底纹时用）
docs/
  方案与计划.md              决策依据与蓝图（含三处官方文档实测结论）
  提交材料.md                作品简介 / 视频脚本 / 提交清单
  AI素材说明.md              Banner 与正片怎么来的、怎么重做
  部署指南.md                Pages + AK 白名单 + 验收
banner/  video/
.github/workflows/pages.yml  推送 main 即自动部署
```

---

## 四、技术要点

1. **积水 ≠ 雨大**。降水只有落在**低洼地面**才会形成高风险积水，所以模型里 lowland 是一个
   *闸门*（`gate = floor + (1-floor)·lowland`），而不是一个加项。这不是抠细节：
   如果没有这个闸门，"绕开积水段"在数学上**完全不起作用**（我们第一版就踩了这个坑，
   见 `docs/AI素材说明.md` 的"踩坑记录"）。
2. **低洼权重由几何决定**：绕行路上的点会**投影回干线**，得到「这是哪一段」（base_km）与
   「横向偏出去多少公里」（lateral_km），后者按余弦衰减决定低洼权重。
   ⚠️ 法向量必须按 (东, 北) 约定算 —— 写成 `(cos(θ+90°), sin(θ+90°))` 会得到
   **部分沿航向**的位移，绕行会塌掉。`validate.mjs` 有专门的回归测试盯着它。
3. **能耗是物理量，不是系数堆**：爬升/回收按 $mgh/\eta$ 算（整车 49 t 爬升 1000 m ≈ 148 kWh），
   再叠加滚阻、风阻（含逆风分量，顺风不倒扣）、空调与雨阻。
4. **迭代收敛**：车速依赖雨 → 到达时刻依赖车速 → 雨又依赖到达时刻，跑 4 轮不动点。
5. **避让区不存在**：实测驾车/骑行/步行 v2 **都没有** `avoid_polygons`（详见 `docs/方案与计划.md` §0.1-B），
   所以改成「绕行示意几何 + 候选集惩罚打分」：把落选方案也画出来，量化暴露并择优。
6. **三方统一择优**：不再分三个模块各出一个结论，而是把
   「就地补能 / 绕行避雨 / 服务区等待」放在同一把尺子下比较 ——
   `综合代价 = 全部耗时 × 1.25 + 风险暴露 × 5.0（折算里程）`，两个系数都可在
   `model.js` 的 `DEFAULTS` 里调，代表调度方的风险偏好。
7. **诚实结论**：跑不完就说跑不完。重载低电量情景会明确给出「不可行」而不是编一个方案出来。

### 用到的百度地图能力

| 能力 | 调用处 | 用途 |
|---|---|---|
| JSAPI GL | `BMapGL.Map` / `Polyline` / `Circle` / `Point` | 底图、干线、高风险段、补给站、告警点 |
| 天气查询 v1（经纬度） | 离线 `tools/fetch_weather.py` | 沿途逐点未来 24 小时逐小时预报 |
| 驾车路线规划 v2 | 在线模式 | 途经点（补给站）、车牌限行、未来出行规划 |
| 地点检索 | 离线 `tools/fetch_stations.py` | 告警点周边充电站/加油站 + `telephone` |

> 坐标换算链 **WGS-84 → GCJ-02 → BD-09** 由 `tools/make_demo_data.py` 本地完成，不消耗配额。

---

## 五、合规章节

- **气象与能耗均为模型预测/估算，不构成行车与调度建议**；页面与 Banner 均已标明。
- 高程/坡度来自**自建字典**（`data/terrain.json`，`source: model`），非实测数据。
- 补给站为依公开资料整理的**示意数据**（`source: curated`），未经地点检索核验；
  上线应替换为真实 POI。
- 底图使用百度地图官方服务，不自行绘制或替换底图。
- 不使用任何非百度第三方**收费**数据源。

---

## 六、待办

- [x] 干线、气象、地形、补给站、情景数据可复现生成
- [x] 预测与优化模型 + 三个候选策略统一择优
- [x] 单页 Demo（地图 / 时间轴 / 剖面图 / 对比卡 / 可解释文本）
- [x] 自检 41 项全绿
- [x] Banner 1200×675 已导出
- [x] 60 秒正片已生成
- [x] **已上线**：<https://kint23.github.io/SunnynPoweredTrip/>（Pages 自动部署，底图与数据均已实测）
- [x] 三档备份已接入（本机 git / `D:\VSbackup\backup-dir` / GitHub，三档 SHA 一致）
- [x] 已配置本站专用浏览器端 AK（`app/config.js`，Referer 白名单需含 `localhost` 与 `kint23.github.io`）
- [ ] 用 `tools/fetch_weather.py` / `fetch_stations.py` 替换为真实气象与真实 POI
- [ ] 在线模式接驾车 v2 实测绕行里程（当前为示意几何）
- [ ] 上传正片到平台并把链接填进报名表
