# 爬虫详情邮箱补齐计划（含不覆盖策略与混淆邮箱识别）

我正在使用 writing-plans 技能创建实现计划。

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在“列表页保存 + 详情页统一补齐”的既有链路里，新增邮箱回填能力：若基础候选无邮箱则从详情页补齐；若已有邮箱则不覆盖；并支持 `name(at)domain(dot)edu`、`name AT domain DOT edu`、`name@domain` 以外的混淆写法。

**架构：** 保持双阶段执行：Agent 仅做列表发现与 `save_professor_candidates`，运行时统一读取所有候选触发详情补齐。将“是否需要补齐”规则改为按需字段（含邮箱）判断，补齐阶段通过 LLM 与文本规则双路径产出候选更新，并由持久化逻辑只更新缺失字段。

**技术栈：** FastAPI、SQLAlchemy AsyncSession、DeepAgents、LangGraph、unittest。

---

## 文件结构

- 修改：`backend/app/services/crawler_tools.py`
  - 责任：增强 `CandidateEnrichmentPayload`、补齐提示词、邮件后处理与混淆邮箱抽取。
- 修改：`backend/app/services/crawl_job_runtime.py`
  - 责任：把 `email` 纳入 `_needs_profile_enrichment`、`_has_any_enrichment`、`_apply_candidate_enrichment` 和日志输出。
- 测试：`backend/test/test_crawler_tools.py`
  - 责任：补齐单元测试（混淆邮箱正则、payload默认值、提示词包含邮箱约束）。
- 测试：`backend/test/test_crawl_job_runtime.py`
  - 责任：补齐时序与数据库结果校验（已有邮箱不改写、缺失邮箱可补齐、仅缺邮箱也会触发补齐）。

## 任务 1：在 `crawler_tools` 增加混淆邮箱识别能力并扩展 enrichment payload

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 测试：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：编写失败测试，验证混淆邮箱标准化方法能还原常见写法**

```python
def test_normalize_obfuscated_email_tokens(self) -> None:
    text = "name (AT) example (dot) edu, another(at)school(dot)cn, third AT example DOT edu.cn"
    normalized = normalize_obfuscated_email_tokens(text)

    self.assertIn("name@example.edu", normalized)
    self.assertIn("another@school.cn", normalized)
    self.assertIn("third@example.edu.cn", normalized)
```

- [ ] **步骤 2：编写失败测试，验证从正文提取混淆邮箱**

```python
def test_extract_email_from_obfuscated_text(self) -> None:
    candidate = extract_first_email_from_text(
        "联系邮箱：zhangsan (at) example (dot) edu；联系方式：lisi AT bupt DOT edu DOT cn"
    )

    self.assertEqual(candidate, "zhangsan@example.edu")
```

- [ ] **步骤 3：运行测试确认当前实现未通过**

运行：`cd backend && uv run python -m unittest test.test_crawler_tools`

预期：FAIL（`normalize_obfuscated_email_tokens` / `extract_first_email_from_text` 不存在，或正向匹配失败）。

- [ ] **步骤 4：实现混淆邮箱归一化与提取方法，并加入到 enrichment 解析流程**

```python
_AT_TOKENS = (r"\(\s*at\s*\)", r"\[\s*at\s*\]", r"\sat\s", r"\sAT\s")
_DOT_TOKENS = (r"\(\s*dot\s*\)", r"\[\s*dot\s*\]", r"\sdot\s", r"\sDOT\s")

def normalize_obfuscated_email_tokens(text: str) -> str:
    cleaned = text
    for token in _AT_TOKENS:
        cleaned = re.sub(token, "@", cleaned, flags=re.IGNORECASE)
    for token in _DOT_TOKENS:
        cleaned = re.sub(token, ".", cleaned, flags=re.IGNORECASE)
    return cleaned

def extract_first_email_from_text(text: str) -> str | None:
    direct = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    if direct:
        return direct[0]
    normalized = normalize_obfuscated_email_tokens(text)
    normalized = re.sub(r"\s+", "", normalized)
    direct = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", normalized)
    return direct[0] if direct else None
```

- [ ] **步骤 5：实现 `CandidateEnrichmentPayload` 增加邮箱字段**

```python
class CandidateEnrichmentPayload(BaseModel):
    email: str | None = None
    department: str | None = None
    research_direction: str | None = None
    recent_papers: list[str] = Field(default_factory=list)
```

- [ ] **步骤 6：更新 `build_candidate_enrichment_prompt`，让 LLM 明确只补缺失字段并避免覆盖邮箱**

```python
return f"""
你正在补齐已发现的导师候选详情。
已知信息：
- 姓名：{candidate.name or "未知"}
- 邮箱：{candidate.email or "未知"}
...
- 允许补齐字段：email、department、research_direction、recent_papers
- 不能覆盖已有字段；只有当该字段为空时才返回。
资料页正文：
{page_text}
"""
```

- [ ] **步骤 7：运行测试确认 `crawler_tools` 相关逻辑通过**

运行：`cd backend && uv run python -m unittest test.test_crawler_tools`

预期：新增测试全部 PASS，现有提取测试保持 PASS。

- [ ] **步骤 8：Commit**

```bash
git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py
git commit -m "feat(crawler): add obfuscated email extraction and enrichment email field"
```

## 任务 2：更新运行时补齐判定与持久化，不覆盖既有邮箱

**文件：**
- 修改：`backend/app/services/crawl_job_runtime.py`
- 测试：`backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：编写失败测试，锁定邮箱缺失触发补齐**

```python
async def test_run_queued_crawl_job_enriches_candidate_email_when_missing(self) -> None:
    ...
    await save_candidates(
        ctx,
        [ProfessorCandidatePayload(name="张三", email=None, profile_url="https://.../a")],
    )
    ...
    # fake enrich 返回 email
    return CandidateEnrichmentPayload(email="zhangsan@example.edu")

    # 最终应写回 email
```

- [ ] **步骤 2：编写失败测试，已有邮箱不允许覆盖**

```python
async def test_run_queued_crawl_job_does_not_overwrite_existing_email(self) -> None:
    ...
    await save_candidates(
        ctx,
        [ProfessorCandidatePayload(name="张三", email="zhang.first@example.edu", profile_url="...")],
    )
    ...
    # fake enrich 返回不同邮箱
    return CandidateEnrichmentPayload(email="zhang.second@example.edu")
    # 最终应仍是 zhang.first@example.edu
```

- [ ] **步骤 3：运行测试确认当前运行时未覆盖且邮箱未参与判定**

运行：`cd backend && uv run python -m unittest test.test_crawl_job_runtime`

预期：FAIL（当前 `_needs_profile_enrichment` 未含 email，`_apply_candidate_enrichment` 不处理 email）。

- [ ] **步骤 4：更新 `_needs_profile_enrichment` 与 `_has_any_enrichment`**

```python
def _needs_profile_enrichment(candidate: CrawlCandidate) -> bool:
    if not candidate.profile_url:
        return False
    return any(
        (
            not candidate.email,
            not (candidate.department or "").strip(),
            not (candidate.research_direction or "").strip(),
            not any(str(item).strip() for item in candidate.recent_papers or []),
        )
    )

def _has_any_enrichment(payload: CandidateEnrichmentPayload) -> bool:
    return bool(
        (payload.email and payload.email.strip())
        or (payload.department and payload.department.strip())
        or ...
    )
```

- [ ] **步骤 5：更新 `_apply_candidate_enrichment`，只在邮箱为空时回填邮箱**

```python
email = update_payload.get("email")
if email and not candidate.email:
    candidate.email = email
    changed = True
```

- [ ] **步骤 6：同步日志字段文案，补齐字段中出现邮箱时也可读**

```python
labels = {"email": "邮箱", "department": "院系", ...}
```

- [ ] **步骤 7：运行测试确认运行时行为通过**

运行：`cd backend && uv run python -m unittest test.test_crawl_job_runtime`

预期：新增缺失邮箱补齐测试 PASS；已有邮箱不覆盖测试 PASS；旧补齐测试保持 PASS。

- [ ] **步骤 8：Commit**

```bash
git add backend/app/services/crawl_job_runtime.py backend/test/test_crawl_job_runtime.py
git commit -m "feat(crawler): only fill missing email in enrichment stage"
```

## 任务 3：把混淆邮箱从详情正文落回 `CandidateEnrichmentPayload` 并做保底规则联动

**文件：**
- 修改：`backend/app/services/crawl_job_runtime.py`
- 修改：`backend/app/services/crawler_tools.py`
- 测试：`backend/test/test_crawl_job_runtime.py`

- [ ] **步骤 1：编写失败测试，验证 LLM 空返回时正文规则抽取仍可得到邮箱**

```python
async def test_run_queued_crawl_job_email_from_text_fallback(self) -> None:
    fake_enrich_with_llm = lambda *_: CandidateEnrichmentPayload()
    crawl_page text includes "联系人：name(AT)univ(DOT)edu"
    assert candidate.email == "name@univ.edu"
```

- [ ] **步骤 2：运行测试确认当前代码未满足回退邮箱覆盖**

运行：`cd backend && uv run python -m unittest test.test_crawl_job_runtime`

预期：FAIL（规则回退未返回 email）。

- [ ] **步骤 3：更新 fallback 规则提取输出，注入邮箱字段**

```python
def extract_candidate_profile_enrichment(text: str) -> dict[str, Any]:
    return {
        "email": extract_first_email_from_text(text),
        "department": _extract_prefixed_line(...),
        ...
    }
```

- [ ] **步骤 4：更新 `test_extract_candidate_profile_enrichment_from_text` 兼容邮箱回退**

```python
updates = extract_candidate_profile_enrichment("邮箱：name(AT)example(DOT)edu\n院系：... ")
self.assertEqual(updates["email"], "name@example.edu")
```

- [ ] **步骤 5：运行测试确认 fallback 通路可回填邮箱**

运行：`cd backend && uv run python -m unittest test.test_crawler_tools test.test_crawl_job_runtime`

预期：PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/services/crawler_tools.py backend/app/services/crawl_job_runtime.py backend/test/test_crawler_tools.py backend/test/test_crawl_job_runtime.py
git commit -m "feat(crawler): fallback parser extracts obfuscated emails"
```

## 任务 4：回归验证与发布前冒烟

**文件：**
- 测试：`backend/test/test_crawler_tools.py`
- 测试：`backend/test/test_crawl_job_runtime.py`
- 测试：`backend/test/test_crawl_jobs_api.py`

- [ ] **步骤 1：运行 crawler 相关测试全集**

运行：`cd backend && uv run python -m unittest test.test_crawler_tools test.test_crawl_job_runtime test.test_crawl_jobs_api`

预期：全部 PASS。

- [ ] **步骤 2：运行项目既有抓取回归命令**

运行：`cd backend && uv run python -m unittest test/Plan_B/faculty-directory-crawler/tests`

预期：该路径下测试 PASS（如存在，若无可记录为“no tests”）。

- [ ] **步骤 3：本地快速手工验证一条真实列表任务（可选）**

```bash
cd backend
uv run uvicorn main:app --reload
```
检查点：
- 列表页候选已落库，且有缺失邮箱时 `profile_url` 触发补齐；
- 补齐阶段仅在字段缺失时更新；
- obfuscate 形式邮箱可进入库里。

- [ ] **步骤 4：Commit**

```bash
git add backend/app/services/crawler_tools.py backend/app/services/crawl_job_runtime.py backend/test/test_crawler_tools.py backend/test/test_crawl_job_runtime.py backend/test/test_crawl_jobs_api.py
git commit -m "feat(crawler): support email enrichment with no-overwrite policy"
```

## 自检

- [ ] 需求“有邮箱不覆盖”在 `_apply_candidate_enrichment` 中已被显式编码（仅 `not candidate.email` 场景写回）。
- [ ] 需求“邮箱缺失也应触发补齐”在 `_needs_profile_enrichment` 中已显式增加 `not candidate.email` 条件。
- [ ] 混淆邮箱解析在 `extract_first_email_from_text` 与 `extract_candidate_profile_enrichment` 中有直接测试与回退测试覆盖。
- [ ] `CandidateEnrichmentPayload`、`_has_any_enrichment`、日志文案与提示词全部同步 `email` 字段。
- [ ] 计划中无占位符（无 TODO/待定/适当处理等字样）。
- [ ] 任务步骤包含失败测试、命令和对应预期。

## 执行交接

计划已更新完成并保存到：`docs/superpowers/plans/2026-04-27-crawler-llm-profile-enrichment.md`

两种执行方式：

1. **子代理驱动（推荐）**  
   - 每个任务单独启动子代理并复核关键实现点；最后做一次交叉回看。

2. **内联执行（executing-plans）**  
   - 由单会话按任务顺序执行，使用检查点回滚与验证。

你先选一种执行方式，我按你选的模式开始落地。 
