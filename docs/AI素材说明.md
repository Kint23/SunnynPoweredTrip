# AI 素材说明（Banner / 正片）

> 2026-09-30 生成。本文件记录素材怎么来的、怎么重做，以及**两条踩过的坑**。

## 一、一句话结论

| 素材 | 做法 | 状态 |
|---|---|---|
| Banner（1200×675） | **纯代码绘制**（Pillow），地图几何直接读 `data/` | 已生成 |
| 60 秒正片 | **真实屏幕录制**（CDP 逐帧采集）+ 字幕烧入 + 自制配乐 | 已生成 |
| README 预览动图 | 从正片抽 3 段拼成动画 WebP | 已生成 |

> 本项目的素材**不依赖任何 AI 生图**：Banner 里的一切（干线、高风险段、绕行线、补给站、KPI 数字）
> 都是从 `data/*.json` **算出来画出来**的。好处很实在 —— 重新生成数据后，图和页面永远一致，
> 不会出现"Banner 上是 46 mm/h、页面上变成 38 mm/h"这种事。

## 二、产物清单

| 文件 | 说明 |
|---|---|
| `banner/晴好出发-banner-1200x675.png` / `.jpg` | **正式 Banner**（89 KB / 105 KB） |
| `banner/teaser.webp` | README 用可点击动图（720×406，10 s，1.8 MB） |
| `video/正片-晴好出发.mp4` | **正片**（60.0 s = 4.0 s 片头 + 55.4 s 实机 / 1920×1080 / 25fps / 6.4 MB / 字幕已烧入 / 自制配乐，无配音） |
| `video/_title.png` | 片头标题卡（1920×1080） |
| `video/subs.ass` | 字幕（ASS，显式 PlayResX/Y=1920/1080）——中间产物 |
| `video/music.wav` | 自制配乐——中间产物 |

## 三、重做命令

```powershell
# ① 数据（改完模型/情景后必须先跑这一步）
uv run tools/make_demo_data.py
node tools/run_model.mjs --update-expect
node tools/validate.mjs                 # 必须 41 项全绿

# ② Banner
uv run tools/make_banner.py

# ③ 正片
uv run python -m http.server 8123 --bind 127.0.0.1     # 另开一个终端
uv run tools/record_demo.py --probe                    # 先自检交互，省一次白录
uv run tools/record_demo.py                            # 录制 + 出成片
uv run tools/record_demo.py --list-only                # 只看时间轴

# ④ README 预览动图（在 video/ 目录下执行，避免滤镜路径里出现盘符冒号）
cd video
ffmpeg -y -ss 0 -t 3 -i "正片-晴好出发.mp4" -ss 15 -t 3 -i "正片-晴好出发.mp4" -ss 30 -t 4 -i "正片-晴好出发.mp4" `
  -filter_complex "[0:v]fps=8,scale=720:-2:flags=lanczos,setsar=1[a];[1:v]fps=8,scale=720:-2:flags=lanczos,setsar=1[b];[2:v]fps=8,scale=720:-2:flags=lanczos,setsar=1[c];[a][b][c]concat=n=3:v=1:a=0[v]" `
  -map "[v]" -c:v libwebp -q:v 68 -loop 0 "..\banner\teaser.webp"
```

改字幕：只改 `tools/record_demo.py` 里的 `SUBS` 列表（时间按**实机段**计，写到 ASS 时自动加片头时长），然后重跑。

## 四、工具环境（已实测）

- **Playwright**（Python）+ Chromium：`uv run` 会自动装；浏览器优先用 `%LOCALAPPDATA%\ms-playwright\chromium-*`
- **ffmpeg 9.0.1**：`%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_...\bin\ffmpeg.exe`
- **Pillow**：`uv run` 自动装
- **Node**：`node tools/run_model.mjs` / `node tools/validate.mjs`（模型与前端共用同一份 `app/model.js`）
- 本机 ComfyUI **没有视频底座模型**，所以片头是"Pillow 标题卡 + 交叉淡化"，不做 text-to-video。

## 五、★ 踩坑记录（这一节最值钱）

### 坑 1：`avoid_polygons` 根本不存在（不是"我写错了"）
PRD 里写"传入 `avoid_polygons` 重规划"，查官方文档后确认：
**驾车/骑行/步行 v2 都没有这个参数**。v2 驾车只有：多备选路线、途经点 ≤18、车牌规避限行、
起点车头方向、未来 7 天出行规划。
而**货车路线规划**（Logistics Direction API）是企业付费产品：最低 5 万配额/10 并发、**10 万/年起**，
且需申请试用权限（1–3 工作日）。
→ 结论：**绕行必须自己算**。本项目实现为「避让锚点 + 候选集惩罚打分」，并把落选方案也画出来。

### 坑 2：绕行的"法向偏移"写错，绕行会**悄悄失效**
第一版用的是：
```js
var perp = (bearing + 90) * Math.PI / 180;
dLng = off / (111.32 * cos(lat)) * Math.cos(perp);
dLat = off / 110.57          * Math.sin(perp);
```
看起来像"旋转 90°"，但在 **(东, 北)** 约定下，航向单位向量是 `(sinθ, cosθ)`，
其左侧法向应当是 `(cosθ, −sinθ)`。上面的写法得到的是 `(−sinθ, cosθ)`，
与航向的点积是 `cos 2θ` —— **不是 0**，也就是说位移里有相当一部分是**沿路方向**的。
后果：绕行路线横向只偏出去 1.8 km（本该 6 km），积水风险几乎没降（53 → 27 而不是 53 → 3.7）。
**这种 bug 不会报错、不会崩，只会让结论变差** —— 是最难发现的一类。
→ 现在 `tools/validate.mjs` 有专门的回归测试：绕行段的横向偏移必须落在 3.0–7.5 km。

### 坑 3：`avoid_polygons` 之外，还有"积水 ≠ 雨大"
最初把低洼当成一个**加项**（`w_rain·雨 + w_low·低洼`），结果全程都有基础风险，
"绕开积水段"在数学上完全不起作用（风险暴露只从 53 降到 27）。
改成**闸门**（`gate = 0.03 + 0.97 × 低洼`）之后才合理：雨大但地势高 ≠ 积水。
→ 建模时先问一句"这个变量是加项还是乘项"，比调参有用得多。

### 坑 4：补给站"已经开过了"却还能被选
最初只按代价排序，没排除**车已经开过**的站（电量告警点在 K320，却发现模型推荐 K42 的站）。
→ 现在 `rankStations` 显式标记 `behind`，页面上也会把这类站显示为「已越过」。
`validate.mjs` 里有一条不变量盯着它。

### 坑 5：录屏采集的四个坑（沿用 ohbj 的结论，实测确认）
1. Playwright `record_video_dir` 的 VP8 **固定 ~0.8 Mbps** → 1080p 的字与地图全糊，必须用 CDP screencast。
2. Chrome **只在画面变化时发帧** → 本次 55 秒只采到 181 帧；必须重采样到 25fps 均匀网格，
   否则时间轴会被压短（ohbj 曾实测"容器报 65s、实际只有 37.6s"）。
3. ffmpeg 滤镜参数里的路径**不能带盘符冒号**（`C:` 会被当选项分隔符）→ 统一 `cwd=video/` 用相对路径。
4. 直接喂 SRT 给 libass 会按 `PlayResY=288` 把字号放大 3.75 倍 → 必须自己写 ASS 并显式声明 `PlayResX/Y`。

### 坑 7：参数"加了但没接上" —— 候选方案声称等待 60 分钟，却没有任何代价
给模型加"服务区避雨等待"这个策略时，我在 `runTrace` 里写了 `pause` 的形参与逻辑，
但**那次批量编辑里这一处实际失败了**（工具报了失败，我没回头核对）。
后果很隐蔽：候选 C 的标签写着「服务区避雨等待 60 分钟」，耗时、能耗、风险暴露
却和「不等待」的候选 A **一模一样** —— 页面上看起来合情合理，实际是**假承诺**。
这类 bug 比崩溃危险得多：崩溃你会去修，"标签与实际不符"你只会信以为真。
→ 现在 `validate.mjs` 有一条不变量专门盯着它：
**任何声明了等待的候选，其耗时必须真的比基线多出至少 90% 的等待时长。**
（修好后 C 变成了一个诚实的方案：等 120 分钟、耗时 +82 分钟，风险暴露 51.7 → 34.2 ——
比绕行方案差，所以模型仍然选 B。）

### 坑 8：无头浏览器与地图
有头模式（默认）下百度地图 WebGL 渲染稳定；`--headless` 时若 WebGL 起不来，
页面会**自动降级**到自绘的离线示意底图（功能完整，只是没有真实底图）——
这条降级路径是故意保留的，它让录屏永远能跑完。
