# 草稿模板改写 Prompt 缓存优化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在不改变草稿模板改写功能效果的前提下，显式拆分稳定前缀与动态后缀，提高同批次多老师改写时的 DeepSeek KV Cache 命中概率。

**架构：** 在 `llm_runtime.py` 中新增 `DraftRewritePromptParts` 与 `build_draft_rewrite_prompt_parts()`，保留现有 `build_draft_rewrite_prompt()` 作为兼容包装。Prompt 继续使用现有 JSON 结构和字段值，但显式构造稳定 payload，再追加 `current_match` 与 `professor` 动态块，并计算 `stable_prefix_hash`。

**技术栈：** Python 3.12、FastAPI 后端、Pydantic、`unittest`、`uv`。

---

## 文件结构

- 修改：`backend/app/services/llm_runtime.py`
  - 新增 `DraftRewritePromptParts` dataclass。
  - 新增 `build_draft_rewrite_prompt_parts()`。
  - 调整 `build_draft_rewrite_prompt()` 为兼容包装。
  - 在模板改写调用路径中保存 `prompt_hash`、`stable_prefix_hash`、`prompt_cache_key` 到 `GeneratedDraftContent`。
- 修改：`backend/test/test_llm_runtime.py`
  - 导入 `build_draft_rewrite_prompt_parts`。
  - 添加稳定前缀顺序与内容隔离测试。
  - 添加生成流程元数据传递测试。
  - 保留现有模板改写测试，确保行为回归不变。

## 实现注意事项

- 不修改请求消息结构：仍为 `system: SYSTEM_DRAFT_REWRITE_PROMPT` 和 `user: JSON Prompt`。
- 不修改字段名、字段值、材料截断规则、模板块序列化方式、老师信息序列化方式。
- 不新增数据库字段，不修改 API schema。
- 不改变 DeepSeek 请求参数；DeepSeek 依赖真实前缀匹配，不依赖 `prompt_cache_key`。
- 所有文件写入使用 UTF-8。

### 任务 1：新增 Prompt Parts 的失败测试

**文件：**
- 修改：`backend/test/test_llm_runtime.py`
- 测试：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：导入待实现函数**

在 `backend/test/test_llm_runtime.py` 的 `from app.services.llm_runtime import (...)` 列表中加入：

```python
    build_draft_rewrite_prompt_parts,
```

- [ ] **步骤 2：编写稳定前缀隔离失败测试**

在 `LLMRuntimeTests` 中、现有草稿改写相关测试附近加入：

```python
    def test_build_draft_rewrite_prompt_parts_separates_stable_prefix_and_dynamic_suffix(self) -> None:
        from app.models import IdentityMaterial, IdentityProfile, LLMProfile, Professor

        identity = IdentityProfile(
            id=1,
            name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        primary_material = IdentityMaterial(
            id=12,
            identity_id=1,
            display_name="简历",
            file_path="data/materials/resume.txt",
            original_filename="resume.txt",
            material_type="resume",
            extracted_text="我做过信息抽取与智能体相关研究。",
        )
        professor = Professor(
            id=5,
            name="李老师",
            email="prof@example.edu",
            title="Professor",
            university="Example University",
            school="Computer Science",
            department="AI",
            research_direction="Information Extraction",
            profile_url="https://example.edu/prof",
            recent_papers=["Paper A"],
        )
        current_match = MatchEvaluationResult(
            match_score=88,
            match_reason="方向匹配",
            fit_points=["信息抽取"],
            risk_points=["背景略泛"],
            keywords=["NLP"],
        )
        document = build_draft_rewrite_document(
            "<p>老师您好，我是{{sender_name}}。</p>",
            {},
        )

        parts = build_draft_rewrite_prompt_parts(
            identity=identity,
            primary_material=primary_material,
            professor=professor,
            available_materials=[primary_material],
            subject_template="申请与{{name}}老师交流",
            source_blocks=document.blocks,
            current_match=current_match,
            rewrite_preferences=DraftRewritePreferences(),
            llm_profile=LLMProfile(
                id=7,
                provider="openai",
                api_base_url=None,
                api_key="test-key",
                model_name="gpt-test",
            ),
        )

        self.assertIn("source_blocks", parts.stable_prefix)
        self.assertIn("我做过信息抽取与智能体相关研究。", parts.stable_prefix)
        self.assertNotIn("李老师", parts.stable_prefix)
        self.assertNotIn("prof@example.edu", parts.stable_prefix)
        self.assertNotIn("Information Extraction", parts.stable_prefix)
        self.assertNotIn("方向匹配", parts.stable_prefix)
        self.assertLess(parts.prompt.index("source_blocks"), parts.prompt.index("current_match"))
        self.assertLess(parts.prompt.index("current_match"), parts.prompt.index("professor"))
        self.assertEqual(len(parts.prompt_hash), 64)
        self.assertEqual(len(parts.stable_prefix_hash), 64)
        self.assertEqual(parts.prompt_cache_key, "draft-rewrite:v3:1:12:5:7")
```

- [ ] **步骤 3：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_build_draft_rewrite_prompt_parts_separates_stable_prefix_and_dynamic_suffix
```

预期：失败，原因是 `build_draft_rewrite_prompt_parts` 尚不存在或无法导入。

- [ ] **步骤 4：Commit 测试红灯**

```powershell
git add backend/test/test_llm_runtime.py
git commit -m "test(llm): cover draft rewrite prompt cache prefix"
```

### 任务 2：实现 Prompt Parts 与兼容包装

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 测试：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：新增 dataclass**

在 `MatchPromptParts` 后加入：

```python
@dataclass(slots=True)
class DraftRewritePromptParts:
    prompt: str
    stable_prefix: str
    prompt_hash: str
    stable_prefix_hash: str
    prompt_cache_key: str | None = None
```

- [ ] **步骤 2：提取稳定 payload 构造函数**

将现有 `build_draft_rewrite_prompt()` 中构造 `payload` 的逻辑保留为新函数主体。新增函数签名：

```python
def build_draft_rewrite_prompt_parts(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    subject_template: str | None,
    source_blocks: list[DraftRewriteSourceBlock],
    current_match: MatchEvaluationResult | None,
    rewrite_preferences: DraftRewritePreferences | None,
    llm_profile: LLMProfile | None = None,
) -> DraftRewritePromptParts:
```

- [ ] **步骤 3：在新函数中先生成稳定前缀**

保持现有 payload 字段和值不变，先不要追加 `current_match` 与 `professor`。稳定 payload 形态如下：

```python
    prompt_input = payload["input"]
    if isinstance(prompt_input, dict):
        if not prompt_input["rewrite_preferences"]:
            del prompt_input["rewrite_preferences"]
        if not prompt_input["user_custom_instruction"]:
            del prompt_input["user_custom_instruction"]

    stable_prefix = json.dumps(payload, ensure_ascii=False, indent=2)
```

- [ ] **步骤 4：追加动态后缀并返回 Parts**

在 `stable_prefix` 计算后追加动态字段，再生成完整 Prompt：

```python
    if isinstance(prompt_input, dict):
        if current_match is not None:
            prompt_input["current_match"] = {
                "match_score": current_match.match_score,
                "match_reason": current_match.match_reason,
                "fit_points": current_match.fit_points,
                "risk_points": current_match.risk_points,
                "keywords": current_match.keywords,
            }
        prompt_input["professor"] = _build_draft_rewrite_professor_context(professor)

    prompt = json.dumps(payload, ensure_ascii=False, indent=2)
    return DraftRewritePromptParts(
        prompt=prompt,
        stable_prefix=stable_prefix,
        prompt_hash=_hash_prompt(prompt),
        stable_prefix_hash=_hash_prompt(stable_prefix),
        prompt_cache_key=(
            _build_draft_rewrite_prompt_cache_key(
                identity=identity,
                primary_material=primary_material,
                professor=professor,
                llm_profile=llm_profile,
            )
            if llm_profile is not None
            else None
        ),
    )
```

- [ ] **步骤 5：将旧函数改为兼容包装**

将 `build_draft_rewrite_prompt()` 保留为：

```python
def build_draft_rewrite_prompt(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    subject_template: str | None,
    source_blocks: list[DraftRewriteSourceBlock],
    current_match: MatchEvaluationResult | None,
    rewrite_preferences: DraftRewritePreferences | None,
) -> str:
    return build_draft_rewrite_prompt_parts(
        identity=identity,
        primary_material=primary_material,
        professor=professor,
        available_materials=available_materials,
        subject_template=subject_template,
        source_blocks=source_blocks,
        current_match=current_match,
        rewrite_preferences=rewrite_preferences,
    ).prompt
```

- [ ] **步骤 6：运行任务 1 测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_build_draft_rewrite_prompt_parts_separates_stable_prefix_and_dynamic_suffix
```

预期：通过。

- [ ] **步骤 7：Commit 实现**

```powershell
git add backend/app/services/llm_runtime.py backend/test/test_llm_runtime.py
git commit -m "feat(llm): expose draft rewrite stable prompt prefix"
```

### 任务 3：锁定批量场景的稳定前缀一致性

**文件：**
- 修改：`backend/test/test_llm_runtime.py`
- 测试：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：编写同批次不同老师稳定前缀一致测试**

在任务 1 新测试后加入：

```python
    def test_draft_rewrite_prompt_parts_keep_same_stable_prefix_for_different_professors(self) -> None:
        from app.models import IdentityMaterial, IdentityProfile, Professor

        identity = IdentityProfile(
            id=1,
            name="张三",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="sender@example.com",
            smtp_password="secret",
            default_language="zh-CN",
            outreach_generation_mode="llm",
        )
        primary_material = IdentityMaterial(
            id=12,
            identity_id=1,
            display_name="简历",
            file_path="data/materials/resume.txt",
            original_filename="resume.txt",
            material_type="resume",
            extracted_text="我做过信息抽取与智能体相关研究。",
        )
        document = build_draft_rewrite_document(
            "<p>老师您好，我是{{sender_name}}。</p>",
            {},
        )
        first = build_draft_rewrite_prompt_parts(
            identity=identity,
            primary_material=primary_material,
            professor=Professor(name="李老师", email="li@example.edu", research_direction="NLP"),
            available_materials=[primary_material],
            subject_template="申请与{{name}}老师交流",
            source_blocks=document.blocks,
            current_match=MatchEvaluationResult(
                match_score=88,
                match_reason="方向匹配",
                fit_points=["信息抽取"],
                risk_points=[],
                keywords=["NLP"],
            ),
            rewrite_preferences=DraftRewritePreferences(),
        )
        second = build_draft_rewrite_prompt_parts(
            identity=identity,
            primary_material=primary_material,
            professor=Professor(name="王老师", email="wang@example.edu", research_direction="Databases"),
            available_materials=[primary_material],
            subject_template="申请与{{name}}老师交流",
            source_blocks=document.blocks,
            current_match=MatchEvaluationResult(
                match_score=72,
                match_reason="数据库方向部分相关",
                fit_points=["数据处理"],
                risk_points=["方向不同"],
                keywords=["Database"],
            ),
            rewrite_preferences=DraftRewritePreferences(),
        )

        self.assertEqual(first.stable_prefix_hash, second.stable_prefix_hash)
        self.assertEqual(first.stable_prefix, second.stable_prefix)
        self.assertNotEqual(first.prompt_hash, second.prompt_hash)
```

- [ ] **步骤 2：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_draft_rewrite_prompt_parts_keep_same_stable_prefix_for_different_professors
```

预期：通过。

- [ ] **步骤 3：Commit 测试**

```powershell
git add backend/test/test_llm_runtime.py
git commit -m "test(llm): lock draft rewrite batch prefix stability"
```

### 任务 4：在生成流程中使用 Prompt Parts 元数据

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 修改：`backend/test/test_llm_runtime.py`
- 测试：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：扩展 GeneratedDraftContent**

将 `GeneratedDraftContent` 修改为：

```python
@dataclass(slots=True)
class GeneratedDraftContent:
    result: DraftGenerationResult
    usage: ChatCompletionUsage | None = None
    prompt_hash: str | None = None
    stable_prefix_hash: str | None = None
    prompt_cache_key: str | None = None
```

- [ ] **步骤 2：编写元数据传递失败测试**

在现有 `test_generate_draft_content_uses_block_prompt_and_keeps_table_html` 中增加断言：

```python
        self.assertIsNotNone(result.prompt_hash)
        self.assertIsNotNone(result.stable_prefix_hash)
        self.assertEqual(result.prompt_cache_key, "draft-rewrite:v3:1:12:1:5")
```

预期：扩展 `GeneratedDraftContent` 前会失败或属性不存在。

- [ ] **步骤 3：运行测试验证失败**

运行：

```powershell
cd backend
uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_generate_draft_content_uses_block_prompt_and_keeps_table_html
```

预期：失败，原因是 `GeneratedDraftContent` 尚未携带对应元数据。

- [ ] **步骤 4：改造模板改写调用路径**

在 `generate_draft_content()` 的模板改写分支中，用 `build_draft_rewrite_prompt_parts()` 替换 `build_draft_rewrite_prompt()`：

```python
        prompt_parts = build_draft_rewrite_prompt_parts(
            identity=identity,
            primary_material=primary_material,
            professor=professor,
            available_materials=available_materials,
            subject_template=custom_subject,
            source_blocks=rewrite_document.blocks,
            current_match=current_match,
            rewrite_preferences=rewrite_preferences,
            llm_profile=llm_profile,
        )
```

然后将请求中的 `content` 改为：

```python
                    "content": prompt_parts.prompt,
```

并将 `prompt_cache_key` 逻辑改为复用 `prompt_parts.prompt_cache_key`：

```python
        if prompt_parts.prompt_cache_key is not None:
            payload["prompt_cache_key"] = prompt_parts.prompt_cache_key
```

返回 `GeneratedDraftContent` 时填充元数据：

```python
        return GeneratedDraftContent(
            result=DraftGenerationResult(
                subject=rendered_subject,
                body_text=rendered.text,
                body_html=rendered.html,
            ),
            usage=completion.usage,
            prompt_hash=prompt_parts.prompt_hash,
            stable_prefix_hash=prompt_parts.stable_prefix_hash,
            prompt_cache_key=prompt_parts.prompt_cache_key,
        )
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```powershell
cd backend
uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_generate_draft_content_uses_block_prompt_and_keeps_table_html
```

预期：通过。

- [ ] **步骤 6：Commit 生成流程改造**

```powershell
git add backend/app/services/llm_runtime.py backend/test/test_llm_runtime.py
git commit -m "feat(llm): propagate draft rewrite prompt cache metadata"
```

### 任务 5：回归验证与格式检查

**文件：**
- 验证：`backend/app/services/llm_runtime.py`
- 验证：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：运行 LLM 运行时测试**

运行：

```powershell
cd backend
uv run python -m unittest test.test_llm_runtime
```

预期：全部通过，输出包含 `OK`。

- [ ] **步骤 2：运行缓存统计相关测试**

运行：

```powershell
cd backend
uv run python -m unittest test.test_crawl_job_runs
```

预期：全部通过，输出包含 `OK`。

- [ ] **步骤 3：检查 diff**

运行：

```powershell
git diff -- backend/app/services/llm_runtime.py backend/test/test_llm_runtime.py
```

预期：diff 只包含 Prompt Parts、模板改写路径元数据和测试；没有无关重构。

- [ ] **步骤 4：Commit 验证收尾**

如果任务 5 发现并修复了小问题，提交：

```powershell
git add backend/app/services/llm_runtime.py backend/test/test_llm_runtime.py
git commit -m "test(llm): verify draft rewrite prompt cache behavior"
```

如果没有新增修改，不需要提交。

## 自检结果

- 规格覆盖：计划覆盖了稳定前缀、动态后缀、Hash 可观测、OpenAI Prompt Cache 兼容、测试锁定顺序和回归验证。
- 范围控制：未包含数据库字段、API schema、调度顺序和非模板草稿生成优化。
- 类型一致性：计划中新增 `DraftRewritePromptParts`、`build_draft_rewrite_prompt_parts()`、`GeneratedDraftContent` 字段名称一致。
- TDD：每个行为变更均先写失败测试，再做最小实现。