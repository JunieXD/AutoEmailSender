# Crawler v2 详情页模式整页抽取实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让智能抓取 v2 在 `entry_type="profile"` 时按用户输入的详情页 URL 进行整页 LLM 抽取，保存 0 或 1 个候选，不创建 chunk，不发现新 URL，并正确处理暂停、取消、转入待审核、token 统计和 debug JSONL。

**架构：** 保持 v2 scheduler 的 work item 类型不变，仍由 `CrawlPageTask` 进入 page worker。page worker 在抓取成功后按 `job.entry_type` 分支：列表页保持 page -> chunk 流程；详情页调用新 profile extraction helper，helper 负责整页 prompt、结构化输出重试、thinking adaptation 透传、token usage 累计和 attempt 调试数据。候选保存复用 `save_candidate_payloads_shared`，但保存上下文要标记 profile 模式，避免把用户输入的详情页当作列表页 URL 清空。

**技术栈：** Python 3、FastAPI service、SQLAlchemy async、Pydantic、unittest、uv、现有 v2 crawler worker/token/debug 工具。

---

## 文件结构

- 创建：`backend/app/services/crawler_v2_profile_extraction.py`：详情页整页抽取 helper，包含 schema、prompt、结构化重试、usage 累计和 attempt debug 数据。
- 修改：`backend/app/services/crawler_v2_page_worker.py`：抓取成功后按 `entry_type` 分支；profile 模式不创建 chunk，调用整页抽取、保存候选、记录 token/debug，并使用 `intent="profile"` 的 browser fallback。
- 修改：`backend/app/services/crawler_tools.py`：给 `CrawlToolContext` 增加任务入口语义，profile 模式不把 `start_url/start_urls/CrawlPageTask` 当作 known listing URL。
- 修改：`backend/test/test_crawler_v2_page_worker.py`：覆盖 profile page worker 成功、no_candidate、暂停后不提交、browser intent、token/debug。
- 创建：`backend/test/test_crawler_v2_profile_extraction.py`：覆盖 helper prompt、结构化重试、usage 累计。
- 修改：`backend/test/test_crawler_tools.py` 或 `backend/test/test_crawler_v2_chunk_worker.py`：覆盖 profile 保存 URL 例外和 list 模式防污染回归。
- 可能修改：`backend/app/services/crawler_v2_scheduler.py` 与 `backend/test/test_crawler_v2_scheduler.py`：只在现有收尾逻辑不能正确处理 profile page terminal 状态时调整。

---

### 任务 1：保存规则例外

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 修改：`backend/test/test_crawler_tools.py`
- 参考：`backend/test/test_crawler_v2_chunk_worker.py` 中现有 listing URL 防污染测试

- [ ] **步骤 1：编写失败测试，profile 模式保留用户输入的详情页 URL**

在 `backend/test/test_crawler_tools.py` 新增异步测试：创建 `CrawlJob(entry_type="profile", start_url="https://example.edu/teacher/zhang.html", start_urls=[同 URL])`，通过 `save_candidate_payloads_shared` 保存 `ProfessorCandidatePayload(name="张三", profile_url=同 URL, source_url=同 URL, source_kind="profile_page")`。断言 `saved_count == 1`，数据库中的 `CrawlCandidate.profile_url` 仍等于该详情页 URL。

- [ ] **步骤 2：编写回归测试，list 模式仍清空列表页 URL**

在同一测试文件新增测试：创建 `CrawlJob(entry_type="list", start_url="https://example.edu/faculty", start_urls=[同 URL])`，候选只有 `name` 和 `profile_url=job.start_url`。调用共享保存路径后断言候选不会以该 `profile_url` 保存；推荐断言 `saved_count == 0` 且 `rejected_count == 1`，因为 URL 被清空后缺少邮箱和主页。

- [ ] **步骤 3：运行测试验证失败**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_tools`

预期：profile 保留测试失败，或 `CrawlToolContext` 暂不接受 `entry_type`。

- [ ] **步骤 4：实现保存上下文开关**

在 `CrawlToolContext` 增加字段：`entry_type: str | None = None`。在 `_save_normalized_candidate_payloads` 中把 known listing URL 初始化改为：`ctx.entry_type != "profile"` 时才调用 `_known_listing_urls_for_job(...)`，然后仍然 `known_listing_urls.update(ctx.known_listing_urls)`。

- [ ] **步骤 5：运行测试验证通过**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_tools test.test_crawler_v2_chunk_worker`

预期：PASS，list/chunk 防污染不回退。

- [ ] **步骤 6：提交**

运行：`git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py backend/test/test_crawler_v2_chunk_worker.py; git commit -m "fix(crawler): preserve profile entry urls during candidate save"`

---

### 任务 2：新增详情页整页抽取 helper

**文件：**
- 创建：`backend/app/services/crawler_v2_profile_extraction.py`
- 创建：`backend/test/test_crawler_v2_profile_extraction.py`
- 参考：`backend/app/services/crawler_v2_chunk_worker.py` 的 `invoke_v2_chunk_agent`、`_extract_message_text`
- 参考：`backend/app/services/llm_runtime.py` 的 `parse_structured_result`

- [ ] **步骤 1：编写失败测试，prompt 包含学校、学院、URL 和整页文本**

创建 `backend/test/test_crawler_v2_profile_extraction.py`。调用 `build_v2_profile_extraction_prompt(university="示例大学", school="计算机学院", source_url="https://example.edu/teacher/zhang.html", title="张三", page_text="张三 教授 邮箱 zhang@example.edu", page_html_excerpt="<h1>张三</h1>")`，断言 prompt 包含学校、学院、URL、正文和 JSON 字段名 `status`。

- [ ] **步骤 2：编写失败测试，非法 JSON 会结构化重试并累计 usage**

在同一测试文件 patch `build_faculty_crawler_model` 返回带 `ainvoke` 的假模型。第一次返回 `content="不是 JSON"` 和 usage，第二次返回 `{"status":"candidate","candidate":{"name":"张三","profile_url":"","source_url":""}}` 和 usage。调用 `invoke_v2_profile_extraction_agent(...)` 后断言 `result.payload["status"] == "candidate"`、`len(result.attempts) == 2`、`result.usage` 为两次 usage 累加。

- [ ] **步骤 3：运行测试验证失败**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_profile_extraction`

预期：FAIL，模块尚不存在。

- [ ] **步骤 4：创建 helper 实现**

实现 `V2ProfileExtractionPayload(BaseModel)`，字段为 `status: str = "no_candidate"` 和 `candidate: dict[str, Any] | None = None`。实现 `V2ProfileExtractionAttempt` 与 `V2ProfileExtractionResult` dataclass，保存 attempt number、raw model text、raw payload、error、usage、累计 usage、page text hash 和长度。

实现 `build_v2_profile_extraction_prompt(...)`，要求模型只输出 JSON，`status` 只能是 `candidate/no_candidate`，不是单个导师详情页则 `no_candidate`，候选必须有 `name`，学校/学院来自用户上下文，`profile_url/source_url` 使用当前详情页 URL。整页文本最多沿用现有抓取上限 12000 字符，HTML 摘要最多 2000 字符。

实现 `invoke_v2_profile_extraction_agent(...)`：通过 `build_faculty_crawler_model(llm_profile, extra_body=thinking_extra_body)` 创建模型；最多 3 次结构化输出尝试；空响应、非法 JSON、schema 错误时追加“上一次输出无法解析，请严格只返回 JSON”的重试提示；每次调用用 `extract_token_usage_from_llm_response` 提取 usage 并累计；成功时返回 `V2ProfileExtractionResult`；全部失败时抛出 `LLMRuntimeError("详情页抽取结构化输出失败: ...")`。

- [ ] **步骤 5：运行测试验证通过**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_profile_extraction`

预期：PASS。

- [ ] **步骤 6：提交**

运行：`git add backend/app/services/crawler_v2_profile_extraction.py backend/test/test_crawler_v2_profile_extraction.py; git commit -m "feat(crawler): add v2 profile extraction helper"`

---

### 任务 3：page worker 接入 profile 分支

**文件：**
- 修改：`backend/app/services/crawler_v2_page_worker.py`
- 修改：`backend/test/test_crawler_v2_page_worker.py`

- [ ] **步骤 1：编写失败测试，profile 成功抽取保存候选且不创建 chunk**

扩展 `_seed_page_task`，允许传 `entry_type`，默认仍是 `list`。新增测试创建 `entry_type="profile"` 的任务，patch `fetch_page_direct` 返回详情页 `PageSnapshot`，patch `ensure_thinking_adaptation` 返回禁用 thinking 的 extra body，patch `invoke_v2_profile_extraction_agent` 返回 `status=candidate`、候选姓名和 usage。运行 page worker 后断言：task succeeded、`CrawlPage` 有记录、`CrawlPageChunk` 为 0、`CrawlCandidate` 保存 1 个、`profile_url/source_url` 均等于当前详情页 URL。

- [ ] **步骤 2：编写失败测试，profile no_candidate 不保存候选并 terminal**

新增测试：helper 返回 `{"status":"no_candidate","candidate":None}`。断言不创建 chunk、不保存候选，`CrawlPageTask.status == failed_terminal`，`last_error` 包含“详情页未识别到导师候选”。

- [ ] **步骤 3：运行测试验证失败**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_page_worker`

预期：profile 测试失败，因为当前 page worker 仍统一创建 chunk。

- [ ] **步骤 4：实现 page worker 分支**

在 `crawler_v2_page_worker.py` 引入 `CrawlWorkerKind`、`ProfessorCandidatePayload`、`save_candidate_payloads_shared`、`invoke_v2_profile_extraction_agent`、`record_crawler_v2_token_usage`、`ensure_thinking_adaptation`。抓取成功并写入 `page_fetched` debug 后：`job.entry_type == "profile"` 时调用 `_extract_profile_for_page_snapshot(...)`，否则保持 `_create_chunks_for_page_snapshot(...)` 和 `page_chunked` 事件。

新增 `_extract_profile_for_page_snapshot`：在 LLM 调用前重新读取 task/job，确认 `_page_task_owned_by_worker` 和 `ensure_job_active`；解析 LLM profile，缺失时 `_mark_page_failed(task, "缺少可用的 LLM Profile")`；调用 `ensure_thinking_adaptation(session, llm_profile)`；写 `profile_extract_requested`；调用 helper；逐 attempt 写 `profile_extract_llm_response`；LLM 返回后调用 `_page_task_can_commit`，失败则写 `profile_extract_skipped_inactive` 并返回。

新增 `_complete_profile_page_extraction`：`status != "candidate"`、`candidate` 非 dict 或 `name` 为空时标记 task `failed_terminal`；否则给候选补齐 `university/school/profile_url/source_url/source_kind="profile_page"/boundary_risk=false`，用 `CrawlToolContext(entry_type="profile")` 调用 `save_candidate_payloads_shared`；保存后标记 task succeeded 并释放 worker 字段；保存结果为 0 且非 rejected 时按现有失败预算语义记录错误。

如果 helper 返回 usage，调用 `record_crawler_v2_token_usage(session_factory, job_id=job_id, worker_kind=CrawlWorkerKind.PAGE, work_item_id=task_id, model_name=llm_profile.model_name, input_tokens=..., output_tokens=..., cached_tokens=..., raw_usage=dict(result.usage))`。

- [ ] **步骤 5：运行测试验证通过**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_page_worker`

预期：PASS，已有 list 模式测试不回退。

- [ ] **步骤 6：提交**

运行：`git add backend/app/services/crawler_v2_page_worker.py backend/test/test_crawler_v2_page_worker.py; git commit -m "feat(crawler): route profile entry pages through full-page extraction"`

---

### 任务 4：补生命周期、token、debug 和 browser intent 覆盖

**文件：**
- 修改：`backend/app/services/crawler_v2_page_worker.py`
- 修改：`backend/test/test_crawler_v2_page_worker.py`

- [ ] **步骤 1：编写失败测试，profile browser fallback 使用 profile intent**

修改或新增 `fetch_page_browser` 测试：调用 `fetch_page_browser(ctx, url, intent="profile")`，patch `browser_investigate`，断言参数为 `goal=""`、`intent="profile"`。再新增 page worker 级测试：profile 任务 direct fetch 失败后 browser fallback 被调用，且调用参数 intent 为 profile。

- [ ] **步骤 2：编写失败测试，LLM 期间暂停后不保存候选**

profile 任务抓取成功后，patch `invoke_v2_profile_extraction_agent` 的 side effect：先把 job 改为 `paused`、task 改为 `pending` 并清空 worker，再返回 candidate payload。断言 worker 返回后没有 `CrawlCandidate`，task 仍为 pending。

- [ ] **步骤 3：编写失败测试，token usage 记录为 PAGE work item**

profile helper 返回 usage `{input_tokens:12, output_tokens:5, cached_tokens:2}`。运行 worker 后查询 `CrawlWorkerTokenUsage`，断言 `worker_kind == CrawlWorkerKind.PAGE.value`、`work_item_id == str(task_id)`、三个 token 字段匹配。

- [ ] **步骤 4：编写失败测试，debug JSONL 事件完整**

patch `append_crawler_v2_debug_event`，helper 返回两个 attempts。断言事件名包含 `page_fetched`、`profile_extract_requested`、两条 `profile_extract_llm_response`、`profile_extract_completed` 或 `profile_extract_no_candidate`。断言 payload 包含 `source_url`、`attempt_number`、`raw_model_text`、`raw_payload`、`token_usage`、`page_text_hash`、`page_text_length`，但不要包含完整 `page_text`。

- [ ] **步骤 5：实现缺失逻辑**

将 `fetch_page_browser(ctx, url)` 改为 `fetch_page_browser(ctx, url, *, intent: str = "generic")`，内部调用 `browser_investigate(ctx, url, goal="", intent=intent)`。page worker 中设置 `fetch_intent = "profile" if job.entry_type == "profile" else "generic"`，所有 browser fallback 均传入该 intent。

确认 `_page_task_can_commit` 检查 task 仍 processing、worker_id 一致、lease 未过期、job active。确认提交候选前和 token usage 记录前均经过该检查。

- [ ] **步骤 6：运行测试验证通过并提交**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_page_worker`

提交：`git add backend/app/services/crawler_v2_page_worker.py backend/test/test_crawler_v2_page_worker.py; git commit -m "test(crawler): cover profile page worker lifecycle and usage"`

---

### 任务 5：调度收尾和转入待审核回归

**文件：**
- 修改：`backend/test/test_crawler_v2_scheduler.py` 或当前负责 v2 收尾的测试文件
- 可能修改：`backend/app/services/crawler_v2_scheduler.py`

- [ ] **步骤 1：定位收尾函数**

运行：`cd C:\StudyPrograms\AutoEmailSender; rg -n "needs_review|failed|finalize|complete|CrawlCandidate|CrawlPageTask" backend/app/services/crawler_v2_scheduler.py backend/test`。记录实际负责“没有开放 work item 后设置 job 状态”的函数名，并在本任务后续测试中调用该真实入口。

- [ ] **步骤 2：编写测试，profile 全部 terminal 且无候选时 job failed**

创建 profile job，包含 1-2 个 `CrawlPageTask(status=failed_terminal)`，没有候选。运行 scheduler 收尾入口后断言 `job.status == failed`，`error_message` 包含“抓取未发现候选导师”。

- [ ] **步骤 3：编写测试，profile 有候选且无开放 work 后 job needs_review**

创建 profile job，所有 page task 都 succeeded 或 failed_terminal，并插入一个 `CrawlCandidate`。运行 scheduler 收尾入口后断言 `job.status == needs_review`。

- [ ] **步骤 4：编写测试，转入待审核后不恢复 profile page task**

定位现有“转入待审核”接口或 service 测试。新增场景：profile job 仍有 pending/processing page task，调用转入待审核后断言这些 page task 变为 terminal 或不会被 scheduler 再领取；随后运行一次 `run_crawler_v2_once`，断言没有新增候选、没有新增 chunk。

- [ ] **步骤 5：运行测试验证失败或确认现有通过**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_scheduler`，必要时追加实际 API 测试文件。

- [ ] **步骤 6：实现缺失收尾逻辑**

如果测试失败，只调整 v2 收尾判断：没有 pending/processing/failed_retryable/split_required 的 page/chunk/enrichment work item 时，根据 `CrawlCandidate` 数量设置 job。候选数大于 0 -> `needs_review`；候选数为 0 -> `failed` 并写“抓取未发现候选导师”。不要把 `failed_terminal` page task 当作开放 work。

- [ ] **步骤 7：运行测试验证通过并提交**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_scheduler`

提交：`git add backend/app/services/crawler_v2_scheduler.py backend/test/test_crawler_v2_scheduler.py; git commit -m "fix(crawler): finalize profile entry jobs after page extraction"`。如果只新增测试且现有实现已通过，提交测试文件即可，commit message 使用 `test(crawler): cover profile entry job finalization`。

---

### 任务 6：集成验证

**文件：**
- 不新增业务改动；只做验证和必要的计划勾选。

- [ ] **步骤 1：运行 focused backend 测试**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest test.test_crawler_v2_page_worker test.test_crawler_v2_profile_extraction test.test_crawler_v2_chunk_worker test.test_crawler_v2_enrichment_worker test.test_crawler_tools test.test_crawler_v2_scheduler`

预期：PASS。

- [ ] **步骤 2：运行 crawl 相关发现测试**

运行：`cd C:\StudyPrograms\AutoEmailSender\backend; uv run python -m unittest discover test -p "test_crawler*.py"`

预期：PASS。

- [ ] **步骤 3：检查 diff 范围**

运行：`cd C:\StudyPrograms\AutoEmailSender; git diff --stat; git diff -- backend/app/services/crawler_v2_page_worker.py backend/app/services/crawler_v2_profile_extraction.py backend/app/services/crawler_tools.py backend/test/test_crawler_v2_page_worker.py backend/test/test_crawler_v2_profile_extraction.py backend/test/test_crawler_tools.py backend/test/test_crawler_v2_scheduler.py`

确认：profile 分支不创建 `CrawlPageChunk`；list 分支仍 chunk；profile 保存上下文设置 `entry_type="profile"`；profile browser fallback 使用 `intent="profile"`；token usage 使用 `CrawlWorkerKind.PAGE` 和 `work_item_id=task_id`；debug event 不保存完整 page input，只保存 hash、长度、模型输出和解析 payload。

- [ ] **步骤 4：最终工作区检查**

运行：`cd C:\StudyPrograms\AutoEmailSender; git status --short`。确认没有意外纳入 `desktop/` 等无关改动。

---

## 自检

- 规格覆盖度：计划覆盖了 profile 不走 chunk、整页 LLM 抽取、thinking adaptation、结构化重试、token 统计、debug JSONL、学校/学院上下文、profile URL 兜底、暂停/取消/转入待审核提交前检查、browser profile intent、保存规则例外和列表页回归。
- 占位符扫描：没有保留“待定”“TODO”“添加适当处理”等占位式步骤；每个任务包含具体文件、测试意图、实现点、命令和预期。
- 类型一致性：新 helper 返回 `V2ProfileExtractionResult`，page worker 使用 `payload`、`usage`、`attempts`、`page_text_hash`、`page_text_length`；保存路径使用现有 `ProfessorCandidatePayload` 和 `save_candidate_payloads_shared`。
- 风险提示：任务 5 的收尾函数名需要执行时按代码定位，因为该计划阶段只确认了 scheduler 的调度职责，没有展开完整收尾实现。执行时必须先完成任务 5 步骤 1。
