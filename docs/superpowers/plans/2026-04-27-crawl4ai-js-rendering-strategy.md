# Crawl4AI 浏览器渲染兜底策略优化

> 目标：把“JS 下才有内容/返回 412”的页面从当前全部 fallback 到 HTTP 的固定失败路径，改为自动走可重试的 Crawl4AI 浏览器渲染策略。

## 现状与问题

- 当前 `crawl_page_with_crawl4ai` 直接调用 `crawl_page_with_http`，遇到 `https://scs.bupt.edu.cn/szjs1/jsyl.htm` 这类页面会返回 412 或空内容。
- 目前 `browser_investigate` 也是占位实现，无法真正执行浏览器探索路径。
- 这会导致：
  1. 教师列表/详情页在 JS 挑战站点抓取失败；
  2. enrichment 阶段拿不到 profile 页面文本；
  3. 任务成功率与可恢复性不足。

## 方案设计

### 策略
- `crawl_page_with_crawl4ai` 改成“多阶段”：
  1. 先走 HTTP（保持现有安全校验与重定向逻辑）；
  2. 对以下条件判定为“疑似 JS/反爬挑战”时触发浏览器模式：
     - 状态码 412；
     - 解析后文本为空（`suspicious_empty=True`）；
     - 命中 body 关键字（如 anti-bot 常见提示文本）；
     - 传入 URL 属于“高风险站点特征”（可选白名单/黑名单规则）。
  3. 浏览器模式成功后再落盘为 `fetch_method="browser"`。
  4. 两次尝试都失败才返回失败快照。
- `browser_investigate` 复用同一浏览器抓取路径：
  - 优先以目标 URL 抓取；
  - 如有 `goal`，通过 `wait_for` 或 `js_code` 注入简单 DOM 判断（如检查目标内容片段是否出现）；
  - 返回同样的 `PageSnapshot` 并保留失败上下文。

### Crawl4AI 配置建议
- 使用 `CrawlerRunConfig`：
  - `process_in_browser=True`
  - `wait_until="networkidle"`
  - `wait_for="css:table"`（默认）以及 `wait_for_timeout=15000`
  - `delay_before_return_html=1.5`
  - `max_retries=2`
  - `viewport` 使用中等值，避免过大内存消耗
  - `user_agent` 明确覆盖为常见浏览器 UA
- 兼容 Windows 无法显示 rich 日志导致乱码问题：调用时统一 `AsyncWebCrawler(verbose=False)`，并在配置中关闭不必要的控制台输出。

### 风险与回退
- 风险：浏览器模式耗时更高；对高并发任务需考虑速率。
- 回退：仅在疑似挑战场景触发浏览器模式，不改变所有 URL 的主路径；保留 HTTP 快速路径。
- 安全：继续保留 `_pre_request_rejected_snapshot` 与 `is_allowed_crawl_url`，避免跨域与私网地址。

## 任务清单

- 文件：`backend/app/services/crawler_tools.py`
  - 新增 `crawl4ai` 分层配置与触发判定逻辑
  - 实现 `_crawl_page_with_crawl4ai` 实际抓取与重试
  - 为 `crawl_page_with_crawl4ai` 增加 HTTP→Browser 回退链
  - 将 `browser_investigate` 改为真实调用浏览器路径（复用公共逻辑）

- 文件：`backend/app/agents/faculty_crawler_agent.py`
  - 在 `investigate_with_browser` 提示词中说明“可用于 JS 页面”与结果预期，避免模型误解

- 文件：`backend/test/test_crawler_tools.py`
  - 增加 3 个单测：
    1. HTTP 路径成功时不进入浏览器重试
    2. 412 + 空内容时触发浏览器抓取
    3. Browser fallback 仍可 fallback 到失败快照（可控 mock）

- 文件：`backend/test/test_crawl_job_runtime.py`
  - 增加一个集成级别验证：`crawl_page_with_crawl4ai` 能正确处理疑似空内容页并推动 enrichment 流程继续

## 验证

- `cd backend && uv run python -m unittest test.test_crawler_tools`
- `cd backend && uv run python -m unittest test.test_crawl_job_runtime`
- 手工抽样：`https://scs.bupt.edu.cn/szjs1/jsyl.htm` 验证抓取文本包含教师姓名且非空。

## Commit 建议

- `feat(crawler): add crawler4ai browser fallback for JS-heavy pages`
