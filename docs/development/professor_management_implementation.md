# 导师管理实现说明

## 1. 前端路由与入口
- 新增路由：`/professors`
- 顶栏新增导航项：`导师管理`
- 首页改成轻入口，只保留跳转按钮，不再直接提供样例导入和抓取按钮

## 2. 后端接口
### 2.1 管理列表与详情
- `GET /api/professors/management?archived=active|archived|all`
- `GET /api/professors/{id}`

### 2.2 模板与导入
- `GET /api/professors/template?format=xlsx|csv`
- `POST /api/professors/import-file`

导入结果结构：
- `inserted_count`
- `updated_count`
- `failed_count`
- `message`

### 2.3 手动维护
- `POST /api/professors`
- `PATCH /api/professors/{id}`

### 2.4 归档与恢复
- `POST /api/professors/{id}/archive`
- `POST /api/professors/bulk-archive`
- `POST /api/professors/{id}/restore`

## 3. 导入规则
- 仅支持 `csv` 和 `xlsx`
- 模板字段固定：
  - `name`
  - `email`
  - `title`
  - `university`
  - `school`
  - `department`
  - `research_direction`
  - `recent_papers`
  - `profile_url`
  - `source_url`
- `recent_papers` 用 `|` 分隔多篇论文标题
- `name` 和 `email` 必填
- 邮箱格式不合法或必填缺失时，该行记为失败
- 同一文件内重复邮箱采用“最后一条覆盖前一条”
- 数据库已有同邮箱导师时执行覆盖更新
- 如果旧导师已归档，导入后会自动恢复

## 4. 归档规则
- 删除不做硬删，只更新 `professors.archived_at`
- 首页列表、创建任务、工作区都只使用未归档导师
- 归档导师仍保留历史任务和通信日志

## 5. 数据库迁移
- 新增 Alembic 迁移：给 `professors` 添加 `archived_at`
- 历史数据默认 `archived_at = NULL`
- 不影响既有导师记录，也不破坏已有任务

## 6. 智能抓取后续增强

- 为抓取任务增加候选审核表格中的批量接受、批量拒绝和单条编辑。
- 为 Agent trace 增加独立查看面板，展示工具调用和失败原因。
- 为智能抓取工具使用 job 级 LLMProfile，避免工具内部硬编码模型。
- 为浏览器抓取批量详情页增加并发限制、最大页数限制和重试策略。
- 为重复邮箱增加并排对比 UI，让用户选择覆盖或跳过。
