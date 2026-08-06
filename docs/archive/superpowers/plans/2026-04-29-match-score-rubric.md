# 匹配度评分量表实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将已确认的匹配度评分量表写入匹配分析提示词，让 LLM 按 3 个维度和上限规则给出 `match_score`。

**架构：** 仅修改匹配分析 system prompt，不改变接口、数据库、前端展示或返回 JSON 结构。用单测锁定 rubric 文案、上限规则和无近期论文场景，保持现有 token 审计与缓存逻辑不变。

**技术栈：** Python、FastAPI 服务层、`unittest`、现有 LLM runtime 测试。

---

## 文件结构

- 修改：`backend/app/services/llm_runtime.py`
  - 职责：维护匹配分析 system prompt，将评分量表和上限规则加入 `SYSTEM_MATCH_ONLY_PROMPT`。
- 修改：`backend/test/test_llm_runtime.py`
  - 职责：为评分量表、上限规则和无近期论文但研究方向具体的 prompt 场景增加回归测试。

---

### 任务 1：用测试锁定评分量表和上限规则

**文件：**
- 修改：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：编写失败的 prompt 文案测试**

在 `LLMRuntimeTests` 中替换现有 `test_match_only_prompt_requires_visible_research_evidence`，让测试检查 3 个评分维度和上限规则：

```python
    def test_match_only_prompt_includes_explicit_score_rubric(self) -> None:
        from app.services.llm_runtime import SYSTEM_MATCH_ONLY_PROMPT

        expected_fragments = [
            "研究主题匹配度：0-50",
            "能力与方法匹配度：0-30",
            "个性化理由充分度：0-20",
            "没有近期论文，但研究方向具体：不限制最高分",
            "没有近期论文，且研究方向很宽泛：match_score 最高 75",
            "没有研究方向，但有近期论文：match_score 最高 85",
            "研究方向和近期论文都缺失：match_score 最高 30",
            "学生默认材料缺少可见研究、项目或技能证据：match_score 最高 60",
            "触发上限规则时，risk_points 必须说明原因",
        ]

        for fragment in expected_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, SYSTEM_MATCH_ONLY_PROMPT)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend
uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_match_only_prompt_includes_explicit_score_rubric
```

预期：FAIL，至少提示 `"研究主题匹配度：0-50" not found`。

- [ ] **步骤 3：Commit 红灯测试**

```bash
git add backend/test/test_llm_runtime.py
git commit -m "test(匹配分析): 覆盖评分量表提示词"
```

---

### 任务 2：实现评分量表提示词

**文件：**
- 修改：`backend/app/services/llm_runtime.py`
- 测试：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：更新 `SYSTEM_MATCH_ONLY_PROMPT`**

在 `额外要求` 前加入以下评分规则，保持 JSON 字段不变：

```python
    评分量表：
    match_score 总分为 100 分，由以下 3 个维度组成。你必须先按维度判断，再给出总分。

    1. 研究主题匹配度：0-50
       衡量默认材料与导师研究方向或近期论文是否在研究问题、应用场景或领域上有交集。
       - 45-50：具体研究问题高度重合。
       - 35-44：同一方向，有明确交集。
       - 20-34：宽泛领域相关，但具体问题不同。
       - 1-19：只有弱相关背景。
       - 0：看不到相关性。

    2. 能力与方法匹配度：0-30
       衡量默认材料中的技能、方法、项目、论文或工具是否能支撑导师方向。
       - 25-30：能力可以直接支撑导师方向。
       - 15-24：有部分可迁移能力。
       - 5-14：只有基础背景或泛化能力。
       - 0：看不到支撑能力。

    3. 个性化理由充分度：0-20
       衡量能否写出具体、可信、不空泛的套磁理由。
       - 16-20：能提炼出具体匹配点。
       - 8-15：能写出合理但不够具体的理由。
       - 1-7：只能泛泛表达兴趣。
       - 0：无法形成可信理由。

    上限规则：
    - 没有近期论文，但研究方向具体：不限制最高分。
    - 没有近期论文，且研究方向很宽泛：match_score 最高 75。
    - 没有研究方向，但有近期论文：match_score 最高 85。
    - 研究方向和近期论文都缺失：match_score 最高 30。
    - 学生默认材料缺少可见研究、项目或技能证据：match_score 最高 60。
    - 触发上限规则时，risk_points 必须说明原因。
```

- [ ] **步骤 2：运行目标测试验证通过**

运行：

```bash
cd backend
uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_match_only_prompt_includes_explicit_score_rubric
```

预期：PASS。

- [ ] **步骤 3：运行 LLM runtime 全量测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_llm_runtime
```

预期：全部测试通过。

- [ ] **步骤 4：Commit prompt 实现**

```bash
git add backend/app/services/llm_runtime.py
git commit -m "feat(匹配分析): 添加显式评分量表"
```

---

### 任务 3：补充无近期论文场景回归测试

**文件：**
- 修改：`backend/test/test_llm_runtime.py`

- [ ] **步骤 1：编写 prompt 构造测试**

在 `LLMRuntimeTests` 中新增测试，确认导师近期论文为空时，prompt 仍完整包含具体研究方向和 `近期论文：- 无`，避免未来改动误把无论文场景过滤掉：

```python
    def test_build_match_prompt_keeps_specific_research_direction_without_recent_papers(self) -> None:
        from app.models import IdentityMaterial, IdentityProfile, Professor

        identity = IdentityProfile(
            id=3,
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
            id=7,
            identity_id=3,
            display_name="简历",
            file_path="data/materials/resume.txt",
            original_filename="resume.txt",
            material_type="resume",
            extracted_text="我做过 biomedical information extraction 与大模型项目。",
        )
        professor = Professor(
            name="李老师",
            email="prof@example.edu",
            title="Professor",
            university="Example University",
            school="Computer Science",
            research_direction="LLM-based biomedical information extraction",
            recent_papers=[],
        )

        parts = build_match_prompt_parts(
            identity=identity,
            primary_material=primary_material,
            professor=professor,
            available_materials=[primary_material],
        )

        self.assertIn("LLM-based biomedical information extraction", parts.prompt)
        self.assertIn("近期论文：\n- 无", parts.prompt)
```

- [ ] **步骤 2：运行新增测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_llm_runtime.LLMRuntimeTests.test_build_match_prompt_keeps_specific_research_direction_without_recent_papers
```

预期：PASS。如果失败，只允许调整 prompt 构造中空近期论文的输出格式，不改变 API。

- [ ] **步骤 3：运行后端相关测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_llm_runtime test.test_match_analysis_runtime
```

预期：全部测试通过。

- [ ] **步骤 4：Commit 回归测试**

```bash
git add backend/test/test_llm_runtime.py
git commit -m "test(匹配分析): 覆盖无近期论文评分场景"
```

---

### 任务 4：最终验证

**文件：**
- 验证：后端 LLM runtime、匹配审计、数据库 schema 测试。

- [ ] **步骤 1：运行后端完整相关测试**

运行：

```bash
cd backend
uv run python -m unittest test.test_database_schema test.test_llm_runtime test.test_match_analysis_runtime
```

预期：`OK`，测试数量应覆盖数据库 schema、LLM runtime、匹配审计 runtime。

- [ ] **步骤 2：检查工作区**

运行：

```bash
git status --short
```

预期：无未提交变更。

- [ ] **步骤 3：整理提交记录**

运行：

```bash
git log --oneline -5
```

预期：能看到评分量表相关的测试和实现提交。
