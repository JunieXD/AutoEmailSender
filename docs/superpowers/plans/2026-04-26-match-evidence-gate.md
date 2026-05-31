# 匹配度证据门槛实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 只有导师具备研究方向或近期论文之一时才允许分析匹配度，同时优化首页文案并收紧模型提示词。

**架构：** 前端在导师行和批量入口处做即时可用性判断，后端在任务运行时做同等校验，避免接口绕过 UI。匹配提示词继续复用现有 JSON 输出结构，只补充证据约束，不引入新字段。

**技术栈：** Vite、React、Vitest、FastAPI、SQLAlchemy、unittest、uv。

---

### 任务 1：前端证据门槛与文案

**文件：**
- 修改：`frontend/src/components/molecules/DashboardProfessorRow.tsx`
- 修改：`frontend/src/pages/HomePage.tsx`
- 测试：`frontend/test/DashboardProfessorRow.test.tsx`

- [ ] **步骤 1：编写失败的组件测试**

```tsx
import { render, screen } from "@testing-library/react";
import { DashboardProfessorRow } from "../src/components/molecules/DashboardProfessorRow";
import type { ProfessorDashboardItemDTO } from "../src/types";

const professor: ProfessorDashboardItemDTO = {
  id: 1,
  name: "无研究信息导师",
  email: "prof@example.edu",
  title: "Professor",
  university: "Example University",
  school: "School of AI",
  department: "CS",
  research_direction: null,
  recent_papers: [],
  profile_url: null,
  source_url: null,
  match_score: null,
  sent_count: 0,
  status: "not_contacted",
};

test("缺少研究方向和近期论文时禁用匹配分析按钮", () => {
  render(
    <DashboardProfessorRow
      professor={professor}
      selected={false}
      bulkDisabled={false}
      scoring={false}
      canCalculateMatch={false}
      matchUnavailableReason="缺少研究方向或近期论文"
      statusLabel="未联系"
      onToggleSelection={() => undefined}
      onCalculateMatch={() => undefined}
      onOpenWorkspace={() => undefined}
    />,
  );

  expect(screen.getByRole("button", { name: "缺少研究信息" })).toBeDisabled();
  expect(screen.getByText("缺少研究方向或近期论文")).toBeInTheDocument();
});
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd frontend && npm run test -- DashboardProfessorRow.test.tsx`
预期：FAIL，原因是测试文件不存在或组件 props 尚不支持 `canCalculateMatch`。

- [ ] **步骤 3：实现最少前端代码**

```tsx
const hasMatchEvidence = (professor: ProfessorDashboardItemDTO) =>
  Boolean(professor.research_direction?.trim()) || professor.recent_papers.some((paper) => paper.trim());
```

在 `HomePage` 中：
- 单个按钮标签改为“分析匹配度”。
- 批量按钮标签改为“批量分析匹配度”。
- 说明文案改为“根据默认材料与导师研究方向/近期论文分析匹配度；草稿请在工作区生成。”
- 单个导师无证据时按钮禁用并显示“缺少研究信息”。
- 批量分析只处理有证据的导师；全都无证据时提示“缺少研究信息”。

- [ ] **步骤 4：运行前端测试验证通过**

运行：`cd frontend && npm run test -- DashboardProfessorRow.test.tsx`
预期：PASS。

### 任务 2：后端兜底校验与提示词收紧

**文件：**
- 修改：`backend/app/services/task_runtime.py`
- 修改：`backend/app/services/llm_runtime.py`
- 测试：`backend/test/test_llm_runtime.py`
- 测试：`backend/test/test_api_endpoints.py`

- [ ] **步骤 1：编写失败的后端测试**

```python
def test_match_prompt_requires_visible_research_evidence(self) -> None:
    from app.services.llm_runtime import SYSTEM_MATCH_ONLY_PROMPT

    self.assertIn("研究方向或近期论文", SYSTEM_MATCH_ONLY_PROMPT)
    self.assertIn("证据不足", SYSTEM_MATCH_ONLY_PROMPT)
```

```python
def test_calculate_match_requires_professor_research_evidence(self) -> None:
    identity_id = self._create_identity(with_imap=False)
    llm_id = self._create_llm()
    professor_response = self.client.post(
        "/api/professors",
        json={
            "name": "缺少研究信息导师",
            "email": "missing-evidence@example.edu",
            "title": "Professor",
            "university": "Example University",
            "school": "School of AI",
            "department": "CS",
            "research_direction": None,
            "recent_papers": [],
            "profile_url": None,
            "source_url": None,
        },
    )
    professor_id = professor_response.json()["id"]
    workspace_response = self.client.post(
        f"/api/workspaces/{professor_id}/ensure-task",
        params={"identity_id": identity_id, "llm_profile_id": llm_id},
    )
    task_id = workspace_response.json()["current_task"]["id"]

    response = self.client.post(f"/api/email-tasks/{task_id}/calculate-match")

    self.assertEqual(response.status_code, 400)
    self.assertEqual(response.json()["detail"], "缺少研究方向或近期论文，暂不能分析匹配度")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && uv run python -m unittest backend.test.test_llm_runtime backend.test.test_api_endpoints.ApiEndpointTests.test_calculate_match_requires_professor_research_evidence`
预期：FAIL，原因是提示词缺少新约束，接口仍会调用模型。

- [ ] **步骤 3：实现最少后端代码**

```python
def _has_professor_match_evidence(professor: Professor) -> bool:
    return bool((professor.research_direction or "").strip()) or any(
        str(paper).strip() for paper in professor.recent_papers or []
    )
```

在 `calculate_task_match` 调用模型前检查；无证据时抛出 `ValueError("缺少研究方向或近期论文，暂不能分析匹配度")`。

在 `SYSTEM_MATCH_ONLY_PROMPT` 增加约束：只基于默认材料与导师研究方向/近期论文中的可见证据评分；证据薄弱时降低分数并在 `risk_points` 说明信息不足。

- [ ] **步骤 4：运行后端测试验证通过**

运行：`cd backend && uv run python -m unittest backend.test.test_llm_runtime backend.test.test_api_endpoints.ApiEndpointTests.test_calculate_match_requires_professor_research_evidence`
预期：PASS。

### 任务 3：收口验证

**文件：**
- 修改：无新增文件
- 测试：前后端相关测试

- [ ] **步骤 1：运行 lint 和相关测试**

运行：`cd frontend && npm run lint`
预期：exit 0。

运行：`cd frontend && npm run test -- DashboardProfessorRow.test.tsx`
预期：PASS。

运行：`cd backend && uv run python -m unittest test.test_llm_runtime test.test_api_endpoints.ApiEndpointTests.test_calculate_match_requires_professor_research_evidence`
预期：PASS。

- [ ] **步骤 2：检查 diff**

运行：`git diff -- frontend/src/components/molecules/DashboardProfessorRow.tsx frontend/src/pages/HomePage.tsx backend/app/services/task_runtime.py backend/app/services/llm_runtime.py frontend/test/DashboardProfessorRow.test.tsx backend/test/test_llm_runtime.py backend/test/test_api_endpoints.py docs/superpowers/plans/2026-04-26-match-evidence-gate.md`
预期：只包含本需求相关变更。
