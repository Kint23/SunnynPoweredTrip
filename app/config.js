/* 百度地图 JSAPI GL 配置 + 加载握手
 *
 * AK 说明
 * ------
 * - 浏览器端 AK 明文写在网页里是**设计使然**：它靠 Referer 白名单保护，不是密钥。
 * - 生产环境请到 https://lbsyun.baidu.com/apiconsole/key 新建一个「浏览器端」AK，
 *   并把 localhost 与线上域名（如 https://kint23.github.io）填进 Referer 白名单。
 * - **服务端 AK 千万不要写在这里**，也不要提交到仓库。
 *
 * 留空会怎样？
 * - 页面**仍然完全可用**：自动切到「离线示意底图」（自绘的干线/雨带/积水段/补给站），
 *   算法、剖面图、对比卡全部照常。只是没有百度真实底图。
 *   这么做是为了让演示与录屏**不依赖网络与配额**，可复现。
 */
window.APP_CONFIG = {
  // 本站专用浏览器端 AK（为「晴好出发」单独创建）。
  // ⚠️ Referer 白名单必须同时包含：localhost、127.0.0.1、kint23.github.io
  //    （缺哪个，那个域名下底图就不出来，会自动降级到离线示意底图）
  // 浏览器端 AK 明文写在网页里是设计使然，靠 Referer 白名单保护，不是密钥。
  ak: "b1h24zdWn8bOMAwpBWJL4e4MqAeSQyRB",

  center: [106.05, 32.65],   // 西安 → 成都 干线中点
  zoom: 7,
  title: "晴好出发 · 基于气象与能耗预测的物流调度"
};

/* ---- JSAPI 加载握手 ----
 * 本文件在 app.js 之前执行，先把回调挂到全局；等地图 API 就绪后再触发 app.js 的启动函数。
 * 只有填了 AK 才会去加载 JSAPI（否则保持纯离线示意模式，不产生任何外部请求）。
 */
(function () {
  var queue = [];
  window.__bmapReadyFired = false;

  window.__bmapReady = function () {
    window.__bmapReadyFired = true;
    var q = queue; queue = [];
    for (var i = 0; i < q.length; i++) { try { q[i](); } catch (e) { console.error(e); } }
  };

  window.onBMapReady = function (fn) {
    if (window.__bmapReadyFired && window.BMapGL) fn();
    else queue.push(fn);
  };

  window.addEventListener("DOMContentLoaded", function () {
    if (!window.APP_CONFIG.ak) return;
    var s = document.createElement("script");
    s.src = "https://api.map.baidu.com/api?v=1.0&type=webgl&ak=" +
            encodeURIComponent(window.APP_CONFIG.ak) + "&callback=__bmapReady";
    s.async = true;
    s.onerror = function () { window.__bmapFailed = true; };
    document.head.appendChild(s);
  });
})();
