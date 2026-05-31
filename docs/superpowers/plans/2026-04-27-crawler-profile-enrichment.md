# 爬虫候选详情补全实现计划

> **面向 AI 代理的工作者：** 必须子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 调整智能爬虫流程，先完成列表页候选发现与保存，再基于已保存候选统一执行一轮资料页补全，优先补抓研究方向、近期论文、院系等详情字段。

**架构：** 将抓取流程拆成“发现阶段”和“补全阶段”两步。发现阶段只负责从列表页提取基础候选并保存；补全阶段从已保存候选中筛选出带 `profile_url` 且详情字段缺失的记录，再统一抓取资料页并更新候选信息。任务最终只有在发现阶段和补全阶段都完成后，才进入待审核。

**技术栈：** FastAPI、SQLAlchemy AsyncSession、DeepAgents、现有 crawler tools、unittest。

---

## 文件结构

- 修改：`backend/app/services/crawler_tools.py`
  责任：为候选保存增加“同 job 下按 email/profile_url 更新空字段”的能力；新增候选详情补全所需的抓取与更新辅助函数。
- 修改：`backend/app/agents/faculty_crawler_agent.py`
  责任：收紧 Agent 提示词，让发现阶段只保存基础候选，不在列表页阶段假装补全详情。
- 修改：`backend/app/services/crawl_job_runtime.py`
  责任：把 crawl job 的运行流程改成“发现完成 -> 统一补全 -> 收尾判定”。
- 修改：`backend/app/services/crawl_job_events.py`
  责任：为补全阶段增加更准确的事件摘要，让执行日志能区分“发现候选”和“详情补全”。
- 可选修改：`backend/app/schemas/crawl_job.py`
  责任：如果需要把补全统计暴露给前端，可在 summary DTO 中增加字段。
- 测试：`backend/test/test_crawler_tools.py`
  责任：覆盖候选保存、候选更新、详情补全抓取和字段合并逻辑。
- 测试：`backend/test/test_crawl_job_runtime.py`
  责任：覆盖“先保存全部候选，再统一补全”的运行时顺序与状态流转。
- 测试：`backend/test/test_crawl_job_events.py`
  责任：覆盖新增补全事件文案。

## 任务 1：定义补全阶段的行为边界

**文件：**
- 修改：`backend/app/agents/faculty_crawler_agent.py`
- 测试：`backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：编写失败测试，固定发现阶段不要求补全详情**

```python
async def test_run_queued_crawl_job_saves_candidates_before_profile_enrichment(self) -> None:
    calls = []

    async def fake_run(ctx, llm_profile, trace_callback=None):
        calls.append("agent-run")
        await save_candidates(
            ctx,
            [
                ProfessorCandidatePayload(
                    name="张三",
                    email="zhang@example.edu",
                    title="教授、博导",
                    profile_url="https://example.edu/faculty/zhang",
                    research_direction=None,
                    recent_papers=[],
                )
            ],
        )
        return {}
```

- [ ] **步骤 2：运行测试验证当前流程缺少“补全阶段”约束**

运行：`D:/Junie/StudyProgram/AutoEmailSender/backend/.venv/Scripts/python.exe -m unittest test.test_crawl_job_runtime`

预期：FAIL，当前实现只有发现后直接收尾，没有统一补全阶段。

- [ ] **步骤 3：修改 Agent 提示词**

```python
FACULTY_CRAWLER_SYSTEM_PROMPT = """...
工具策略：
- 列表页阶段优先提取姓名、邮箱、基础职称和 profile_url。
- 如果列表页已拿到候选，不要求在同一步补齐研究方向或论文。
- 发现阶段只保存基础候选；详情补全由后端统一阶段完成。
..."""
```

- [ ] **步骤 4：运行相关测试验证提示词改动未破坏现有工具边界**

运行：`D:/Junie/StudyProgram/AutoEmailSender/backend/.venv/Scripts/python.exe -m unittest test.test_crawl_job_runtime test.test_crawl_job_events`

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/agents/faculty_crawler_agent.py backend/test/test_crawl_job_runtime.py
git commit -m "refactor(crawler): separate discovery and enrichment stages"
```

## 任务 2：为候选保存增加“补全更新”能力

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 测试：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写失败测试，固定同 job 下允许用详情补全更新已有候选**

```python
async def test_save_candidates_updates_existing_candidate_missing_fields(self) -> None:
    async with _RealCrawlerSessionHarness() as harness:
        job_id = await harness.create_job()
        ctx = CrawlToolContext(
            job_id=job_id,
            start_url="https://example.edu/faculty",
            university="示例大学",
            school="计算机学院",
            session_factory=harness.session_factory,
        )

        await save_candidates(
            ctx,
            [
                ProfessorCandidatePayload(
                    name="张三",
                    email="zhang@example.edu",
                    title="教授、博导",
                    profile_url="https://example.edu/faculty/zhang",
                )
            ],
        )

        saved = await save_candidates(
            ctx,
            [
                ProfessorCandidatePayload(
                    name="张三",
                    email="zhang@example.edu",
                    title="教授、博导",
                    profile_url="https://example.edu/faculty/zhang",
                    research_direction="大语言模型",
                    recent_papers=["Paper A"],
                )
            ],
        )

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].research_direction, "大语言模型")
        self.assertEqual(saved[0].recent_papers, ["Paper A"])
```

- [ ] **步骤 2：运行测试验证当前保存逻辑只会跳过重复候选**

运行：`D:/Junie/StudyProgram/AutoEmailSender/backend/.venv/Scripts/python.exe -m unittest test.test_crawler_tools`

预期：FAIL，当前逻辑遇到重复 email 会直接 continue。

- [ ] **步骤 3：实现候选更新辅助逻辑**

```python
def _merge_candidate_payload(existing: CrawlCandidate, payload: dict[str, Any]) -> bool:
    changed = False
    for field in ("title", "department", "research_direction", "profile_url", "source_url"):
        incoming = payload.get(field)
        if incoming and not getattr(existing, field):
            setattr(existing, field, incoming)
            changed = True

    incoming_papers = payload.get("recent_papers") or []
    if incoming_papers and not (existing.recent_papers or []):
        existing.recent_papers = incoming_papers
        changed = True

    return changed
```

```python
async def save_candidates(...):
    existing_candidates = await _load_existing_candidates(session, ctx.job_id)
    ...
    if identity_key in existing_candidates:
        changed = _merge_candidate_payload(existing_candidates[identity_key], payload)
        if changed:
            saved.append(existing_candidates[identity_key])
        continue
```

- [ ] **步骤 4：补充 identity key 规则**

```python
def _candidate_identity_key(payload: dict[str, Any]) -> str | None:
    email = payload.get("email")
    if email:
        return f"email:{email.lower()}"
    profile_url = payload.get("profile_url")
    if profile_url:
        return f"profile:{profile_url}"
    return None
```

- [ ] **步骤 5：运行测试验证保存和补全更新逻辑**

运行：`D:/Junie/StudyProgram/AutoEmailSender/backend/.venv/Scripts/python.exe -m unittest test.test_crawler_tools`

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py
git commit -m "feat(crawler): allow candidate enrichment updates"
```

## 任务 3：新增统一的资料页补全阶段

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 修改：`backend/app/services/crawl_job_runtime.py`
- 测试：`backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：编写失败测试，固定运行顺序为“保存全部候选 -> 统一补全 -> 收尾”**

```python
async def test_run_queued_crawl_job_enriches_profiles_after_discovery(self) -> None:
    sequence = []

    async def fake_run(ctx, llm_profile, trace_callback=None):
        sequence.append("discover")
        await save_candidates(
            ctx,
            [
                ProfessorCandidatePayload(
                    name="张三",
                    email="zhang@example.edu",
                    title="教授、博导",
                    profile_url="https://example.edu/faculty/zhang",
                )
            ],
        )
        return {}

    async def fake_crawl_page_with_crawl4ai(ctx, url):
        sequence.append(f"enrich:{url}")
        return PageSnapshot(
            url=url,
            title="张三",
            text="研究方向：大语言模型\n代表论文：Paper A",
            html="<html></html>",
            links=[],
            fetch_method="http",
            status="succeeded",
        )

    self.assertEqual(sequence, ["discover", "enrich:https://example.edu/faculty/zhang"])
```

- [ ] **步骤 2：运行测试验证当前运行器没有补全阶段**

运行：`D:/Junie/StudyProgram/AutoEmailSender/backend/.venv/Scripts/python.exe -m unittest test.test_crawl_job_runtime`

预期：FAIL。

- [ ] **步骤 3：实现候选补全筛选与抓取**

```python
async def enrich_saved_candidates_once(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: int,
) -> int:
    async with session_factory() as session:
        candidates = list(
            (
                await session.execute(
                    select(CrawlCandidate)
                    .where(
                        CrawlCandidate.job_id == job_id,
                        CrawlCandidate.profile_url.is_not(None),
                    )
                    .order_by(CrawlCandidate.created_at.asc(), CrawlCandidate.id.asc())
                )
            ).scalars()
        )

    enriched = 0
    for candidate in candidates:
        if not _needs_profile_enrichment(candidate):
            continue
        snapshot = await crawl_page_with_crawl4ai(ctx, candidate.profile_url)
        updates = _extract_candidate_profile_enrichment(snapshot.text)
        enriched += await _apply_candidate_enrichment(session_factory, candidate.id, updates)
    return enriched
```

- [ ] **步骤 4：在运行器中插入补全阶段**

```python
try:
    await run_faculty_crawler_agent(...)
except ...:
    ...
else:
    await enrich_saved_candidates_once(session_factory, job_id)
    await _complete_running_job(session_factory, job_id)
```

- [ ] **步骤 5：为补全阶段写 trace 事件**

```python
await _append_agent_trace(
    session_factory,
    job_id,
    {
        "event_type": "enrichment",
        "message": f"开始补全候选导师详情：{candidate.name}",
        "created_at": datetime.now(UTC).isoformat(),
        "raw": {"candidate_id": candidate.id, "profile_url": candidate.profile_url},
    },
)
```

- [ ] **步骤 6：运行运行时测试验证顺序和状态流转**

运行：`D:/Junie/StudyProgram/AutoEmailSender/backend/.venv/Scripts/python.exe -m unittest test.test_crawl_job_runtime`

预期：PASS。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/services/crawler_tools.py backend/app/services/crawl_job_runtime.py backend/test/test_crawl_job_runtime.py
git commit -m "feat(crawler): enrich saved candidates after discovery"
```

## 任务 4：定义详情页信息提取规则

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 测试：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写失败测试，固定从详情页文本中提取研究方向和论文**

```python
def test_extract_candidate_profile_enrichment_from_text(self) -> None:
    updates = _extract_candidate_profile_enrichment(
        "研究方向：大语言模型、智能体\n代表论文：Paper A；Paper B\n院系：计算机科学系"
    )

    self.assertEqual(updates["research_direction"], "大语言模型、智能体")
    self.assertEqual(updates["recent_papers"], ["Paper A", "Paper B"])
    self.assertEqual(updates["department"], "计算机科学系")
```

- [ ] **步骤 2：实现最小规则提取器**

```python
def _extract_candidate_profile_enrichment(text: str) -> dict[str, Any]:
    return {
        "department": _extract_prefixed_line(text, ("院系：", "部门：")),
        "research_direction": _extract_prefixed_line(text, ("研究方向：", "研究领域：")),
        "recent_papers": _extract_paper_list(text),
    }
```

- [ ] **步骤 3：运行测试验证规则提取**

运行：`D:/Junie/StudyProgram/AutoEmailSender/backend/.venv/Scripts/python.exe -m unittest test.test_crawler_tools`

预期：PASS。

- [ ] **步骤 4：Commit**

```bash
git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py
git commit -m "feat(crawler): extract profile enrichment fields from detail pages"
```

## 任务 5：让执行日志能看懂发现与补全阶段

**文件：**
- 修改：`backend/app/services/crawl_job_events.py`
- 测试：`backend/test/test_crawl_job_events.py`

- [ ] **步骤 1：编写失败测试，固定补全阶段文案**

```python
def test_trace_enrichment_message_is_human_readable(self) -> None:
    message = summarize_agent_trace_event(
        {
            "event_type": "enrichment",
            "message": "开始补全候选导师详情：张三",
        }
    )

    self.assertEqual(message, "开始补全候选导师详情：张三")
```

- [ ] **步骤 2：在事件汇总中保留 enrichment 阶段文案**

```python
if event_type == "enrichment" and isinstance(message, str) and message.strip():
    return message.strip()
```

- [ ] **步骤 3：运行测试验证执行日志摘要**

运行：`D:/Junie/StudyProgram/AutoEmailSender/backend/.venv/Scripts/python.exe -m unittest test.test_crawl_job_events`

预期：PASS。

- [ ] **步骤 4：Commit**

```bash
git add backend/app/services/crawl_job_events.py backend/test/test_crawl_job_events.py
git commit -m "refactor(crawler): clarify enrichment events in execution logs"
```

## 任务 6：全链路回归验证

**文件：**
- 测试：`backend/test/test_crawler_tools.py`
- 测试：`backend/test/test_crawl_job_runtime.py`
- 测试：`backend/test/test_crawl_job_events.py`
- 测试：`backend/test/test_crawl_jobs_api.py`

- [ ] **步骤 1：运行抓虫相关后端测试集**

运行：
`D:/Junie/StudyProgram/AutoEmailSender/backend/.venv/Scripts/python.exe -m unittest test.test_crawler_tools test.test_crawl_job_runtime test.test_crawl_job_events test.test_crawl_jobs_api`

预期：PASS。

- [ ] **步骤 2：手工验证一条真实抓取任务**

运行：
`cd backend && uv run uvicorn main:app --reload`

手工检查：
- 列表页候选先被发现并保存
- 所有候选保存后，才开始详情补全
- 待审核前，候选中至少部分记录已有研究方向或近期论文
- 执行日志能区分“发现候选”和“补全详情”

- [ ] **步骤 3：Commit（如第 1、2 步发现问题则先修复再提交）**

```bash
git add backend/app/services/crawler_tools.py backend/app/services/crawl_job_runtime.py backend/app/services/crawl_job_events.py backend/test/test_crawler_tools.py backend/test/test_crawl_job_runtime.py backend/test/test_crawl_job_events.py backend/test/test_crawl_jobs_api.py
git commit -m "feat(crawler): add post-discovery profile enrichment flow"
```

## 自检

- [ ] 计划中的每个改动文件都已经在“文件结构”中列出。
- [ ] 没有使用“后续实现”“待定”“补充细节”这类占位语。
- [ ] 每个任务都包含具体文件、测试命令和预期结果。
- [ ] 方案明确遵循“先保存全部候选，再统一补全”的用户决策，没有写成“保存一条就立刻补全”。
- [ ] 没有把详情补全完全交给 Agent 自由决定，而是通过后端统一阶段控制执行顺序。

## 执行交接

计划已完成并保存到 `docs/superpowers/plans/2026-04-27-crawler-profile-enrichment.md`。

两种执行方式：

**1. 子代理驱动（推荐）** - 每个任务调度一个新的子代理，任务间进行审查，快速迭代  
**2. 内联执行** - 在当前会话中使用 executing-plans 执行任务，批量执行并设有检查点

选哪种方式？  
如果选子代理驱动：必须子技能使用 `superpowers:subagent-driven-development`。  
如果选内联执行：必须子技能使用 `superpowers:executing-plans`。  
