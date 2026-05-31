# 抓取研究方向与近期论文归一化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在抓取、导入、手动更新三条路径上统一 `research_direction`/`recent_papers` 归一化规则，并将 `recent_papers` 稳定限制为前 8 篇。

**架构：** 新增一个纯函数归一化模块，集中实现分隔、去空、保序去重、上限截断；后端 schema 与抓取服务统一调用该模块；抓取提示词强制 LLM 输出数组，服务端继续兜底解析字符串；前端导入提示文案与规则对齐。

**技术栈：** Python 3.12、FastAPI/Pydantic、unittest、React/Vitest。

---

## 文件结构（先锁定边界）

- 创建：`backend/app/services/professor_field_normalization.py`
- 创建：`backend/test/test_professor_field_normalization.py`
- 修改：`backend/app/services/crawler_tools.py`
- 修改：`backend/app/schemas/professor.py`
- 修改：`backend/app/schemas/crawl_job.py`
- 修改：`backend/app/services/professor_management.py`
- 修改：`backend/test/test_crawler_tools.py`
- 修改：`backend/test/test_professor_management.py`
- 修改：`backend/test/test_api_endpoints.py`
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
- 修改：`frontend/test/ProfessorsPageNotifications.test.tsx`

职责划分：
- `professor_field_normalization.py` 只负责字段归一化（无 DB、无网络依赖）。
- `schemas/*` 负责 API 输入规范化。
- `crawler_tools.py` 负责抓取候选归一化与 LLM 提示词约束。
- `professor_management.py` 负责导入模板说明与导入解析一致性。
- `frontend/*` 只负责提示文案与界面预期。

### 任务 1：抽离统一归一化模块（TDD）

**文件：**
- 创建：`backend/app/services/professor_field_normalization.py`
- 测试：`backend/test/test_professor_field_normalization.py`

- [ ] **步骤 1：先写失败测试，锁定规则**

```python
# backend/test/test_professor_field_normalization.py
import unittest

from app.services.professor_field_normalization import (
    RECENT_PAPERS_MAX_ITEMS,
    normalize_recent_papers,
    normalize_research_direction,
)


class ProfessorFieldNormalizationTests(unittest.TestCase):
    def test_normalize_research_direction_list_to_chinese_semicolon(self) -> None:
        self.assertEqual(
            normalize_research_direction([" 大模型 ", "", "智能体", "信息抽取"]),
            "大模型；智能体；信息抽取",
        )

    def test_normalize_recent_papers_string_split_trim_dedupe_and_cap(self) -> None:
        raw = "Paper A|Paper B；Paper A\nPaper C;Paper D|Paper E|Paper F|Paper G|Paper H|Paper I"
        self.assertEqual(
            normalize_recent_papers(raw),
            ["Paper A", "Paper B", "Paper C", "Paper D", "Paper E", "Paper F", "Paper G", "Paper H"],
        )
        self.assertEqual(RECENT_PAPERS_MAX_ITEMS, 8)

    def test_normalize_recent_papers_list_keeps_order_and_caps(self) -> None:
        raw = [f" Paper {index} " for index in range(1, 12)]
        self.assertEqual(normalize_recent_papers(raw), [f"Paper {index}" for index in range(1, 9)])
```

- [ ] **步骤 2：运行测试，确认失败**

运行：`cd backend && uv run python -m unittest test.test_professor_field_normalization -v`  
预期：`ModuleNotFoundError: No module named 'app.services.professor_field_normalization'`

- [ ] **步骤 3：实现最小归一化模块**

```python
# backend/app/services/professor_field_normalization.py
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

RECENT_PAPERS_MAX_ITEMS = 8
RECENT_PAPERS_SPLIT_PATTERN = re.compile(r"[|；;\n]+")


def normalize_research_direction(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "；".join(parts) or None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    stripped = str(value).strip()
    return stripped or None


def normalize_recent_papers(value: Any, *, max_items: int = RECENT_PAPERS_MAX_ITEMS) -> list[str]:
    raw_items: list[str]
    if value is None:
        raw_items = []
    elif isinstance(value, str):
        raw_items = [item.strip() for item in RECENT_PAPERS_SPLIT_PATTERN.split(value) if item.strip()]
    elif isinstance(value, Iterable):
        raw_items = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw_items = []

    deduped: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
        if len(deduped) >= max_items:
            break
    return deduped
```

- [ ] **步骤 4：运行测试，确认通过**

运行：`cd backend && uv run python -m unittest test.test_professor_field_normalization -v`  
预期：3 个测试全部 `OK`

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/professor_field_normalization.py backend/test/test_professor_field_normalization.py
git commit -m "feat(backend): add shared professor field normalization helpers"
```

### 任务 2：接入 schema 与导入链路，统一上限 8

**文件：**
- 修改：`backend/app/schemas/professor.py`
- 修改：`backend/app/schemas/crawl_job.py`
- 修改：`backend/app/services/professor_management.py`
- 测试：`backend/test/test_professor_management.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：补充失败测试，覆盖导入超限与多分隔符**

```python
# backend/test/test_professor_management.py
def test_parse_csv_import_caps_recent_papers_to_first_8(self) -> None:
    csv_content = (
        ",".join(PROFESSOR_TEMPLATE_COLUMNS)
        + "\n"
        + "张三,zhang@example.edu,教授,示例大学,人工智能学院,计算机系,方向A,Paper1|Paper2|Paper3|Paper4|Paper5|Paper6|Paper7|Paper8|Paper9|Paper10,,\n"
    ).encode("utf-8-sig")
    parsed = parse_professor_import_file("professors.csv", csv_content)
    self.assertEqual(
        parsed.data["zhang@example.edu"]["recent_papers"],
        ["Paper1", "Paper2", "Paper3", "Paper4", "Paper5", "Paper6", "Paper7", "Paper8"],
    )
```

```python
# backend/test/test_api_endpoints.py（在导入断言处新增）
self.assertEqual(
    li_professor["recent_papers"],
    ["Paper 1", "Paper 2", "Paper 3", "Paper 4", "Paper 5", "Paper 6", "Paper 7", "Paper 8"],
)
```

- [ ] **步骤 2：运行测试，确认当前失败**

运行：  
`cd backend && uv run python -m unittest test.test_professor_management.ProfessorManagementServiceTests.test_parse_csv_import_caps_recent_papers_to_first_8 -v`  
`cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_professor_template_download_and_import_file_upserts_existing_records -v`  
预期：`recent_papers` 断言失败（当前未统一截断到 8）

- [ ] **步骤 3：在 schema 与导入解析中接入共享归一化**

```python
# backend/app/schemas/professor.py
from app.services.professor_field_normalization import normalize_recent_papers
...
@field_validator("recent_papers", mode="before")
@classmethod
def _normalize_recent_papers(cls, value: object) -> list[str]:
    return normalize_recent_papers(value)
```

```python
# backend/app/schemas/crawl_job.py
from app.services.professor_field_normalization import normalize_recent_papers
...
@field_validator("recent_papers", mode="before")
@classmethod
def _normalize_recent_papers(cls, value: object) -> list[str]:
    return normalize_recent_papers(value)
```

```python
# backend/app/services/professor_management.py
from app.services.professor_field_normalization import normalize_recent_papers
...
def _parse_recent_papers(value: str | None) -> list[str]:
    return normalize_recent_papers(value)
```

- [ ] **步骤 4：更新模板说明文本（导入规则对齐）**

```python
# backend/app/services/professor_management.py
TEMPLATE_HELP_ROWS = [
    ...
    "# recent_papers：近期论文，多篇用 | 分隔；最多保留前 8 篇。示例：Paper A|Paper B",
    ...
]
```

- [ ] **步骤 5：运行相关测试，确认通过**

运行：  
`cd backend && uv run python -m unittest test.test_professor_management -v`  
`cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_professor_template_download_and_import_file_upserts_existing_records -v`  
预期：全部 `OK`

- [ ] **步骤 6：Commit**

```bash
git add backend/app/schemas/professor.py backend/app/schemas/crawl_job.py backend/app/services/professor_management.py backend/test/test_professor_management.py backend/test/test_api_endpoints.py
git commit -m "feat(backend): enforce recent_papers cap in schema and import paths"
```

### 任务 3：接入抓取链路与提示词数组约束

**文件：**
- 修改：`backend/app/services/crawler_tools.py`
- 测试：`backend/test/test_crawler_tools.py`

- [ ] **步骤 1：补充失败测试（抓取输入宽松 + 前 8 截断 + 提示词数组要求）**

```python
# backend/test/test_crawler_tools.py
def test_professor_candidate_payload_normalizes_recent_papers_string_with_multi_separators(self) -> None:
    candidate = ProfessorCandidatePayload.model_validate(
        {"name": "张三", "recent_papers": "Paper A；Paper B|Paper C\nPaper D"}
    )
    self.assertEqual(candidate.recent_papers, ["Paper A", "Paper B", "Paper C", "Paper D"])

def test_normalize_candidate_payload_caps_recent_papers_to_first_8(self) -> None:
    payload = normalize_candidate_payload(
        ProfessorCandidatePayload(
            name="张三",
            recent_papers=[f"Paper {index}" for index in range(1, 12)],
        ),
        university="示例大学",
        school="计算机学院",
    )
    self.assertEqual(payload["recent_papers"], [f"Paper {index}" for index in range(1, 9)])

def test_build_profile_candidate_prompt_requires_recent_papers_json_array(self) -> None:
    prompt = build_profile_candidate_prompt(
        university="示例大学",
        school="计算机学院",
        profile_url="https://example.edu/faculty/zhang",
        page_text="研究方向：智能体",
    )
    self.assertIn('recent_papers 必须是 JSON 数组', prompt)
```

- [ ] **步骤 2：运行测试，确认失败**

运行：  
`cd backend && uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_professor_candidate_payload_normalizes_recent_papers_string_with_multi_separators -v`  
`cd backend && uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_normalize_candidate_payload_caps_recent_papers_to_first_8 -v`  
`cd backend && uv run python -m unittest test.test_crawler_tools.CrawlerToolTests.test_build_profile_candidate_prompt_requires_recent_papers_json_array -v`  
预期：至少 1 个断言失败

- [ ] **步骤 3：实现抓取链路归一化与提示词约束**

```python
# backend/app/services/crawler_tools.py
from app.services.professor_field_normalization import (
    RECENT_PAPERS_MAX_ITEMS,
    normalize_recent_papers,
    normalize_research_direction,
)
...
@field_validator("research_direction", mode="before")
@classmethod
def _normalize_research_direction(cls, value: object) -> object:
    return normalize_research_direction(value)

@field_validator("recent_papers", mode="before")
@classmethod
def _normalize_recent_papers(cls, value: object) -> list[str]:
    return normalize_recent_papers(value)
...
papers = normalize_recent_papers(candidate.recent_papers, max_items=RECENT_PAPERS_MAX_ITEMS)
...
- recent_papers 必须是 JSON 数组，例如 ["Paper A", "Paper B"]；不要输出拼接字符串。
```

- [ ] **步骤 4：运行抓取相关测试，确认通过**

运行：`cd backend && uv run python -m unittest test.test_crawler_tools -v`  
预期：`CrawlerToolTests` 全部 `OK`

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/crawler_tools.py backend/test/test_crawler_tools.py
git commit -m "feat(crawler): enforce recent_papers array normalization and cap"
```

### 任务 4：前端提示文案与回归测试对齐

**文件：**
- 修改：`frontend/src/pages/ProfessorsPage.tsx`
- 测试：`frontend/test/ProfessorsPageNotifications.test.tsx`

- [ ] **步骤 1：先写失败测试（导入提示包含上限 8）**

```tsx
// frontend/test/ProfessorsPageNotifications.test.tsx
expect(screen.getByText(/recent_papers/)).toBeInTheDocument();
expect(screen.getByText(/最多保留前 8 篇/)).toBeInTheDocument();
```

- [ ] **步骤 2：运行测试，确认失败**

运行：`cd frontend && npm run test -- ProfessorsPageNotifications.test.tsx`  
预期：找不到 “最多保留前 8 篇”

- [ ] **步骤 3：更新页面提示文案**

```tsx
// frontend/src/pages/ProfessorsPage.tsx
<li>
  <span className="font-mono text-xs">recent_papers</span>{" "}
  多篇论文用 | 分隔；同邮箱会覆盖更新；最多保留前 8 篇。
</li>
```

- [ ] **步骤 4：运行前端测试，确认通过**

运行：`cd frontend && npm run test -- ProfessorsPageNotifications.test.tsx`  
预期：全部 `PASS`

- [ ] **步骤 5：Commit**

```bash
git add frontend/src/pages/ProfessorsPage.tsx frontend/test/ProfessorsPageNotifications.test.tsx
git commit -m "chore(frontend): align import guidance with recent_papers cap"
```

### 任务 5：端到端回归与收尾

**文件：**
- 测试：`backend/test/test_api_endpoints.py`
- 测试：`frontend/test/TasksPageCrawler.test.tsx`（仅回归运行，不改代码）

- [ ] **步骤 1：运行后端核心回归**

运行：  
`cd backend && uv run python -m unittest test.test_api_endpoints.ApiEndpointTests.test_professor_template_download_and_import_file_upserts_existing_records -v`  
`cd backend && uv run python -m unittest test.test_crawler_tools -v`  
预期：全部 `OK`

- [ ] **步骤 2：运行前端抓取详情回归**

运行：`cd frontend && npm run test -- TasksPageCrawler.test.tsx`  
预期：全部 `PASS`，候选详情弹窗仍可渲染多篇论文列表

- [ ] **步骤 3：运行最小静态检查**

运行：`cd frontend && npm run lint`  
预期：无新增 lint 错误

- [ ] **步骤 4：最终 Commit（如前序任务已逐步提交，可跳过）**

```bash
git status
git log --oneline -n 5
```

预期：工作区干净，提交历史包含本计划对应的实现提交。

## 规格覆盖自检

- 规格要求“输入宽松 + 存储统一”：任务 1、2、3 覆盖。
- 规格要求“recent_papers 上限 8，保留前 8”：任务 1、2、3 的测试与实现覆盖。
- 规格要求“提示词强制数组 + 服务端兜底”：任务 3 覆盖。
- 规格要求“导入/前端提示一致”：任务 2 与任务 4 覆盖。
- 规格要求“回归验证”：任务 5 覆盖。

## 执行选项

计划已完成并保存到 `docs/superpowers/plans/2026-05-02-crawler-research-direction-recent-papers-normalization.md`。两种执行方式：

1. 子代理驱动（推荐） - 每个任务调度一个新的子代理，任务间进行审查，快速迭代
2. 内联执行 - 在当前会话中使用 executing-plans 执行任务，批量执行并设有检查点

选哪种方式？
