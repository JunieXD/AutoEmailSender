# Token 消耗历史分页与趋势图实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将个人中心 Token 消耗记录中心扩展为支持历史分页、功能筛选、日期小时范围筛选和输入/输出堆叠柱状趋势图。

**架构：** 扩展现有后端聚合 service 和 FastAPI 路由，由后端负责筛选、计数、分页和图表分桶。前端新增 typed API、日期/分页/图表工具函数，并把 `TokenUsageCenterCard` 拆成筛选表单、分页列表和趋势图小组件。

**技术栈：** FastAPI、SQLAlchemy async、Pydantic、unittest、React 19、TypeScript、Vitest、Testing Library、Tailwind CSS。

---

## 文件结构

- 修改：`backend/app/schemas/token_usage.py`
  - 新增分页 DTO、图表 DTO、查询枚举类型。
- 修改：`backend/app/services/token_usage_records.py`
  - 拆出候选记录读取、功能过滤、时间过滤、分页、summary、图表分桶。
- 修改：`backend/app/api/token_usage.py`
  - 为 records 接口增加分页和筛选参数，新增 chart 接口。
- 修改：`backend/test/test_token_usage_records.py`
  - 扩展现有临时 SQLite 测试，覆盖分页、功能筛选、时间范围和图表分桶。
- 修改：`frontend/src/types/index.ts`
  - 新增 pagination、chart、filter DTO 类型。
- 修改：`frontend/src/lib/api/tokenUsage.ts`
  - 扩展 `listTokenUsageRecords` 参数，新增 `getTokenUsageChart`。
- 修改：`frontend/src/features/token-usage/client/tokenUsage.ts`
  - 新增页号校验、日期小时转换、图表柱高计算、preset label 工具。
- 修改：`frontend/src/features/token-usage/client/tokenUsage.test.ts`
  - 覆盖新增前端工具函数。
- 修改：`frontend/test/TokenUsageCenterCard.test.tsx`
  - 覆盖展开加载第一页、筛选查询、页号跳转、图表请求。
- 修改：`frontend/src/components/molecules/TokenUsageCenterCard.tsx`
  - 拆分内部小组件，实现筛选、分页、图表。

---

### 任务 1：后端记录分页与筛选红灯测试

**文件：**
- 修改：`backend/test/test_token_usage_records.py`

- [ ] **步骤 1：扩展测试数据种子**

在 `TokenUsageRecordsServiceTests` 中新增 `_seed_history_records`，保留现有 `_seed_records` 不动，用于兼容已有测试。

```python
    async def _seed_history_records(self) -> None:
        base = datetime(2026, 4, 30, 10, 0, 0, tzinfo=UTC)
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="博士申请邮箱",
                profile_name="博士申请邮箱",
                sender_name="王同学",
                email_address="sender@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="llm",
            )
            llm_profile = LLMProfile(
                name="OpenAI",
                provider="openai",
                api_key="test-key",
                model_name="gpt-test",
            )
            professor = Professor(
                name="李老师",
                email="li@example.edu",
                university="示例大学",
                school="计算机学院",
                research_direction="信息抽取",
            )
            session.add_all([identity, llm_profile, professor])
            await session.flush()

            task = EmailTask(
                identity_id=identity.id,
                llm_profile_id=llm_profile.id,
                professor_id=professor.id,
                selected_material_ids=[],
            )
            session.add(task)
            await session.flush()

            for index in range(7):
                created_at = base - timedelta(hours=index)
                session.add(
                    MatchAnalysisRun(
                        email_task_id=task.id,
                        professor_id=professor.id,
                        identity_id=identity.id,
                        llm_profile_id=llm_profile.id,
                        success=True,
                        match_score=80 + index,
                        prompt_tokens=100 + index,
                        completion_tokens=10 + index,
                        cached_tokens=5 * index,
                        total_tokens=110 + index * 2,
                        created_at=created_at,
                    )
                )

            crawl_job = CrawlJob(
                university="示例大学",
                school="计算机学院",
                start_url="https://example.edu/faculty",
                status=CrawlJobStatus.COMPLETED.value,
                progress_current=3,
                progress_total=3,
                llm_profile_id=llm_profile.id,
                created_at=base - timedelta(hours=8),
                updated_at=base - timedelta(hours=8),
            )
            session.add(crawl_job)
            await session.flush()
            session.add(
                CrawlJobRun(
                    job_id=crawl_job.id,
                    attempt_number=1,
                    status=CrawlJobStatus.COMPLETED.value,
                    input_tokens=800,
                    output_tokens=120,
                    total_tokens=920,
                    created_at=base - timedelta(hours=8),
                    updated_at=base - timedelta(hours=8),
                )
            )

            session.add(
                EmailLog(
                    email_task_id=task.id,
                    identity_id=identity.id,
                    llm_profile_id=llm_profile.id,
                    professor_id=professor.id,
                    direction=EmailDirection.DRAFT.value,
                    subject="申请交流",
                    content="李老师您好",
                    provider_payload={
                        "source": "llm",
                        "usage": {
                            "prompt_tokens": 500,
                            "completion_tokens": 60,
                            "total_tokens": 560,
                        },
                    },
                    created_at=base - timedelta(hours=9),
                )
            )

            await session.commit()
```

- [ ] **步骤 2：编写 service 分页测试**

在同一测试类中新增：

```python
    def test_lists_records_with_pagination(self) -> None:
        self._run_async(self._seed_history_records())

        async def run_query():
            async with self.session_factory() as session:
                return await list_token_usage_records(session, page=2, page_size=5)

        result = self._run_async(run_query())

        self.assertEqual(result.pagination.page, 2)
        self.assertEqual(result.pagination.page_size, 5)
        self.assertEqual(result.pagination.total_records, 9)
        self.assertEqual(result.pagination.total_pages, 2)
        self.assertEqual(len(result.records), 4)
        self.assertEqual(result.records[0].feature_type, "match_analysis")
        self.assertEqual(result.summary.record_count, 9)
```

- [ ] **步骤 3：编写功能筛选和时间筛选测试**

新增：

```python
    def test_filters_records_by_feature_and_time_range(self) -> None:
        self._run_async(self._seed_history_records())
        start_at = datetime(2026, 4, 30, 6, 0, 0, tzinfo=UTC)
        end_at = datetime(2026, 4, 30, 8, 0, 0, tzinfo=UTC)

        async def run_query():
            async with self.session_factory() as session:
                return await list_token_usage_records(
                    session,
                    page=1,
                    page_size=5,
                    feature_type="match_analysis",
                    start_at=start_at,
                    end_at=end_at,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.pagination.total_records, 3)
        self.assertEqual([item.feature_type for item in result.records], ["match_analysis"] * 3)
        self.assertEqual(
            [item.created_at.hour for item in result.records],
            [8, 7, 6],
        )
```

- [ ] **步骤 4：编写 API 参数测试**

新增：

```python
    def test_api_returns_paginated_filtered_records(self) -> None:
        self._run_async(self._seed_history_records())

        from app.core.database import get_async_session
        from main import create_app

        async def override_session():
            async with self.session_factory() as session:
                yield session

        app = create_app()
        app.dependency_overrides[get_async_session] = override_session
        client = TestClient(app)
        try:
            response = client.get(
                "/api/token-usage/records",
                params={
                    "page": 2,
                    "page_size": 5,
                    "feature_type": "match_analysis",
                },
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["pagination"]["page"], 2)
        self.assertEqual(payload["pagination"]["page_size"], 5)
        self.assertEqual(payload["pagination"]["total_records"], 7)
        self.assertEqual(len(payload["records"]), 2)
```

- [ ] **步骤 5：运行红灯测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_token_usage_records.TokenUsageRecordsServiceTests.test_lists_records_with_pagination test.test_token_usage_records.TokenUsageRecordsServiceTests.test_filters_records_by_feature_and_time_range test.test_token_usage_records.TokenUsageRecordsServiceTests.test_api_returns_paginated_filtered_records
```

预期：FAIL，报错包含 `got an unexpected keyword argument 'page'` 或响应缺少 `pagination`。

- [ ] **步骤 6：提交红灯测试**

```bash
git add backend/test/test_token_usage_records.py
git commit -m "test(token): 覆盖历史分页和筛选"
```

---

### 任务 2：后端记录分页与筛选实现

**文件：**
- 修改：`backend/app/schemas/token_usage.py`
- 修改：`backend/app/services/token_usage_records.py`
- 修改：`backend/app/api/token_usage.py`
- 测试：`backend/test/test_token_usage_records.py`

- [ ] **步骤 1：扩展后端 schema**

在 `backend/app/schemas/token_usage.py` 中加入：

```python
from datetime import datetime
from typing import Literal

TokenUsageFeatureFilter = Literal["all", "crawl", "match_analysis", "draft_generation"]


class TokenUsagePaginationRead(BaseModel):
    page: int
    page_size: int
    total_records: int
    total_pages: int
```

并修改 `TokenUsageRecordListRead`：

```python
class TokenUsageRecordListRead(BaseModel):
    records: list[TokenUsageRecordRead] = Field(default_factory=list)
    summary: TokenUsageSummaryRead
    pagination: TokenUsagePaginationRead
```

- [ ] **步骤 2：修改 service 签名和分页逻辑**

把 `list_token_usage_records` 改为：

```python
async def list_token_usage_records(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 5,
    feature_type: str = "all",
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> TokenUsageRecordListRead:
    if start_at is not None and end_at is not None and start_at > end_at:
        raise ValueError("开始时间不能晚于结束时间")

    resolved_page = max(page, 1)
    resolved_page_size = min(max(page_size, 1), 100)
    candidates = await _list_all_candidate_records(session)
    filtered = _filter_records(
        candidates,
        feature_type=feature_type,
        start_at=start_at,
        end_at=end_at,
    )
    records = sorted(filtered, key=lambda item: item.created_at, reverse=True)
    total_records = len(records)
    total_pages = (total_records + resolved_page_size - 1) // resolved_page_size
    start_index = (resolved_page - 1) * resolved_page_size
    page_records = records[start_index : start_index + resolved_page_size]

    return TokenUsageRecordListRead(
        records=page_records,
        summary=_build_summary(records),
        pagination=TokenUsagePaginationRead(
            page=resolved_page,
            page_size=resolved_page_size,
            total_records=total_records,
            total_pages=total_pages,
        ),
    )
```

需要同步从 schema 导入 `TokenUsagePaginationRead`，并从 `datetime` 导入 `datetime`。

- [ ] **步骤 3：新增候选和过滤函数**

在同一 service 中新增：

```python
async def _list_all_candidate_records(session: AsyncSession) -> list[TokenUsageRecordRead]:
    candidates: list[TokenUsageRecordRead] = []
    candidates.extend(await _list_crawl_records(session, limit=None))
    candidates.extend(await _list_match_records(session, limit=None))
    candidates.extend(await _list_draft_records(session, limit=None))
    return candidates


def _filter_records(
    records: list[TokenUsageRecordRead],
    *,
    feature_type: str,
    start_at: datetime | None,
    end_at: datetime | None,
) -> list[TokenUsageRecordRead]:
    filtered = records
    if feature_type != "all":
        filtered = [item for item in filtered if item.feature_type == feature_type]
    if start_at is not None:
        filtered = [item for item in filtered if item.created_at >= start_at]
    if end_at is not None:
        filtered = [item for item in filtered if item.created_at <= end_at]
    return filtered
```

把 `_list_crawl_records`、`_list_match_records`、`_list_draft_records` 的 `limit` 参数改为 `int | None`，并只在 `limit is not None` 时调用 `.limit(limit)`。

- [ ] **步骤 4：扩展 API 参数并处理时间错误**

在 `backend/app/api/token_usage.py` 中修改 records 路由：

```python
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from app.schemas.token_usage import TokenUsageFeatureFilter, TokenUsageRecordListRead


@router.get("/records", response_model=TokenUsageRecordListRead)
async def list_records(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=5, ge=1, le=100),
    feature_type: TokenUsageFeatureFilter = Query(default="all"),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> TokenUsageRecordListRead:
    try:
        return await list_token_usage_records(
            session,
            page=page,
            page_size=page_size,
            feature_type=feature_type,
            start_at=start_at,
            end_at=end_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
```

- [ ] **步骤 5：兼容旧测试**

更新 `test_lists_recent_function_level_token_records` 和 `test_api_returns_token_usage_records` 里的调用：

```python
return await list_token_usage_records(session, page=1, page_size=20)
```

API 测试把 `limit=2` 改为：

```python
response = client.get("/api/token-usage/records?page=1&page_size=2")
```

- [ ] **步骤 6：运行后端记录测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_token_usage_records
```

预期：PASS。

- [ ] **步骤 7：提交实现**

```bash
git add backend/app/schemas/token_usage.py backend/app/services/token_usage_records.py backend/app/api/token_usage.py backend/test/test_token_usage_records.py
git commit -m "feat(token): 支持历史分页和筛选"
```

---

### 任务 3：后端图表接口红灯测试

**文件：**
- 修改：`backend/test/test_token_usage_records.py`

- [ ] **步骤 1：编写小时粒度图表 service 测试**

新增：

```python
    def test_builds_hourly_chart_for_recent_6_hours(self) -> None:
        self._run_async(self._seed_history_records())
        now = datetime(2026, 4, 30, 10, 0, 0, tzinfo=UTC)

        async def run_query():
            async with self.session_factory() as session:
                from app.services.token_usage_records import build_token_usage_chart

                return await build_token_usage_chart(
                    session,
                    feature_type="match_analysis",
                    preset="last_6_hours",
                    now=now,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.granularity, "hour")
        self.assertEqual(len(result.buckets), 6)
        self.assertEqual(result.buckets[-1].bucket_label, "10:00")
        self.assertEqual(result.buckets[-1].input_tokens, 100)
        self.assertEqual(result.buckets[-1].output_tokens, 10)
```

- [ ] **步骤 2：编写自定义范围自动粒度测试**

新增：

```python
    def test_custom_chart_uses_daily_granularity_for_long_ranges(self) -> None:
        self._run_async(self._seed_history_records())
        start_at = datetime(2026, 4, 27, 0, 0, 0, tzinfo=UTC)
        end_at = datetime(2026, 4, 30, 10, 0, 0, tzinfo=UTC)

        async def run_query():
            async with self.session_factory() as session:
                from app.services.token_usage_records import build_token_usage_chart

                return await build_token_usage_chart(
                    session,
                    feature_type="all",
                    preset="custom",
                    start_at=start_at,
                    end_at=end_at,
                    now=end_at,
                )

        result = self._run_async(run_query())

        self.assertEqual(result.granularity, "day")
        self.assertGreaterEqual(len(result.buckets), 4)
        self.assertEqual(result.buckets[-1].bucket_label, "04-30")
        self.assertGreater(result.buckets[-1].input_tokens, 0)
```

- [ ] **步骤 3：编写 chart API 测试**

新增：

```python
    def test_api_returns_chart_buckets(self) -> None:
        self._run_async(self._seed_history_records())

        from app.core.database import get_async_session
        from main import create_app

        async def override_session():
            async with self.session_factory() as session:
                yield session

        app = create_app()
        app.dependency_overrides[get_async_session] = override_session
        client = TestClient(app)
        try:
            response = client.get(
                "/api/token-usage/chart",
                params={
                    "preset": "custom",
                    "feature_type": "match_analysis",
                    "start_at": "2026-04-30T08:00:00+00:00",
                    "end_at": "2026-04-30T10:00:00+00:00",
                },
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertEqual(payload["granularity"], "hour")
        self.assertEqual(len(payload["buckets"]), 3)
        self.assertEqual(payload["buckets"][-1]["bucket_label"], "10:00")
```

- [ ] **步骤 4：运行红灯测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_token_usage_records.TokenUsageRecordsServiceTests.test_builds_hourly_chart_for_recent_6_hours test.test_token_usage_records.TokenUsageRecordsServiceTests.test_custom_chart_uses_daily_granularity_for_long_ranges test.test_token_usage_records.TokenUsageRecordsServiceTests.test_api_returns_chart_buckets
```

预期：FAIL，报错包含 `cannot import name 'build_token_usage_chart'` 或 404。

- [ ] **步骤 5：提交红灯测试**

```bash
git add backend/test/test_token_usage_records.py
git commit -m "test(token): 覆盖消耗趋势图"
```

---

### 任务 4：后端图表接口实现

**文件：**
- 修改：`backend/app/schemas/token_usage.py`
- 修改：`backend/app/services/token_usage_records.py`
- 修改：`backend/app/api/token_usage.py`
- 测试：`backend/test/test_token_usage_records.py`

- [ ] **步骤 1：新增图表 schema**

在 `backend/app/schemas/token_usage.py` 中加入：

```python
TokenUsageChartPreset = Literal["last_6_hours", "last_24_hours", "last_7_days", "custom"]
TokenUsageChartGranularity = Literal["hour", "day"]


class TokenUsageChartBucketRead(BaseModel):
    bucket_start: datetime
    bucket_label: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class TokenUsageChartRead(BaseModel):
    preset: TokenUsageChartPreset
    granularity: TokenUsageChartGranularity
    range_start: datetime
    range_end: datetime
    buckets: list[TokenUsageChartBucketRead] = Field(default_factory=list)
```

- [ ] **步骤 2：实现时间范围和桶生成函数**

在 `backend/app/services/token_usage_records.py` 中加入：

```python
from datetime import UTC, datetime, timedelta
from app.schemas.token_usage import TokenUsageChartBucketRead, TokenUsageChartRead


async def build_token_usage_chart(
    session: AsyncSession,
    *,
    feature_type: str = "all",
    preset: str = "last_24_hours",
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    now: datetime | None = None,
) -> TokenUsageChartRead:
    resolved_now = _normalize_datetime(now or datetime.now(UTC))
    range_start, range_end, granularity = _resolve_chart_range(
        preset=preset,
        start_at=start_at,
        end_at=end_at,
        now=resolved_now,
    )
    candidates = await _list_all_candidate_records(session)
    filtered = _filter_records(
        candidates,
        feature_type=feature_type,
        start_at=range_start,
        end_at=range_end,
    )
    buckets = _build_chart_buckets(
        filtered,
        range_start=range_start,
        range_end=range_end,
        granularity=granularity,
    )
    return TokenUsageChartRead(
        preset=preset,
        granularity=granularity,
        range_start=range_start,
        range_end=range_end,
        buckets=buckets,
    )
```

新增辅助函数：

```python
def _resolve_chart_range(
    *,
    preset: str,
    start_at: datetime | None,
    end_at: datetime | None,
    now: datetime,
) -> tuple[datetime, datetime, str]:
    aligned_now = _floor_to_hour(now)
    if preset == "last_6_hours":
        return aligned_now - timedelta(hours=5), aligned_now, "hour"
    if preset == "last_24_hours":
        return aligned_now - timedelta(hours=23), aligned_now, "hour"
    if preset == "last_7_days":
        end_day = _floor_to_day(now)
        return end_day - timedelta(days=6), end_day, "day"
    if preset != "custom":
        raise ValueError("不支持的图表范围")
    if start_at is None or end_at is None:
        raise ValueError("自定义图表范围需要开始时间和结束时间")
    resolved_start = _normalize_datetime(start_at)
    resolved_end = _normalize_datetime(end_at)
    if resolved_start > resolved_end:
        raise ValueError("开始时间不能晚于结束时间")
    granularity = "hour" if resolved_end - resolved_start <= timedelta(hours=48) else "day"
    return (
        _floor_to_hour(resolved_start) if granularity == "hour" else _floor_to_day(resolved_start),
        _floor_to_hour(resolved_end) if granularity == "hour" else _floor_to_day(resolved_end),
        granularity,
    )
```

新增分桶函数：

```python
def _build_chart_buckets(
    records: list[TokenUsageRecordRead],
    *,
    range_start: datetime,
    range_end: datetime,
    granularity: str,
) -> list[TokenUsageChartBucketRead]:
    step = timedelta(hours=1) if granularity == "hour" else timedelta(days=1)
    starts: list[datetime] = []
    cursor = range_start
    while cursor <= range_end:
        starts.append(cursor)
        cursor += step

    totals = {
        bucket_start: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        for bucket_start in starts
    }
    for record in records:
        bucket_start = (
            _floor_to_hour(record.created_at)
            if granularity == "hour"
            else _floor_to_day(record.created_at)
        )
        if bucket_start not in totals:
            continue
        totals[bucket_start]["input_tokens"] += record.input_tokens or 0
        totals[bucket_start]["output_tokens"] += record.output_tokens or 0
        totals[bucket_start]["total_tokens"] += record.total_tokens or 0

    return [
        TokenUsageChartBucketRead(
            bucket_start=bucket_start,
            bucket_label=_format_bucket_label(bucket_start, granularity=granularity),
            input_tokens=values["input_tokens"],
            output_tokens=values["output_tokens"],
            total_tokens=values["total_tokens"],
        )
        for bucket_start, values in totals.items()
    ]
```

新增 datetime 工具：

```python
def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _floor_to_hour(value: datetime) -> datetime:
    normalized = _normalize_datetime(value)
    return normalized.replace(minute=0, second=0, microsecond=0)


def _floor_to_day(value: datetime) -> datetime:
    normalized = _normalize_datetime(value)
    return normalized.replace(hour=0, minute=0, second=0, microsecond=0)


def _format_bucket_label(value: datetime, *, granularity: str) -> str:
    return value.strftime("%H:00") if granularity == "hour" else value.strftime("%m-%d")
```

- [ ] **步骤 3：新增 chart API**

在 `backend/app/api/token_usage.py` 中加入：

```python
from app.schemas.token_usage import TokenUsageChartPreset, TokenUsageChartRead
from app.services.token_usage_records import build_token_usage_chart


@router.get("/chart", response_model=TokenUsageChartRead)
async def chart(
    feature_type: TokenUsageFeatureFilter = Query(default="all"),
    preset: TokenUsageChartPreset = Query(default="last_24_hours"),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> TokenUsageChartRead:
    try:
        return await build_token_usage_chart(
            session,
            feature_type=feature_type,
            preset=preset,
            start_at=start_at,
            end_at=end_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
```

- [ ] **步骤 4：运行图表测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_token_usage_records
```

预期：PASS。

- [ ] **步骤 5：提交实现**

```bash
git add backend/app/schemas/token_usage.py backend/app/services/token_usage_records.py backend/app/api/token_usage.py backend/test/test_token_usage_records.py
git commit -m "feat(token): 添加输入输出趋势图接口"
```

---

### 任务 5：前端 API 类型与工具红灯测试

**文件：**
- 修改：`frontend/src/features/token-usage/client/tokenUsage.test.ts`

- [ ] **步骤 1：编写工具函数红灯测试**

在 `frontend/src/features/token-usage/client/tokenUsage.test.ts` 中扩展 import：

```typescript
import {
  buildTokenUsageQueryParams,
  calculateStackedBarSegments,
  formatDateTimeLocalValue,
  isValidPageJump,
  parseDateTimeLocalValue,
} from './tokenUsage';
```

新增测试：

```typescript
  it('validates page jumps', () => {
    expect(isValidPageJump(1, 3)).toBe(true);
    expect(isValidPageJump(3, 3)).toBe(true);
    expect(isValidPageJump(0, 3)).toBe(false);
    expect(isValidPageJump(4, 3)).toBe(false);
  });

  it('converts datetime-local values to iso strings', () => {
    expect(parseDateTimeLocalValue('')).toBeNull();
    expect(parseDateTimeLocalValue('2026-04-30T10:00')).toContain('2026-04-30T');
  });

  it('formats iso strings for datetime-local inputs', () => {
    expect(formatDateTimeLocalValue(null)).toBe('');
    expect(formatDateTimeLocalValue('2026-04-30T10:00:00.000Z')).toMatch(
      /^2026-04-30T/,
    );
  });

  it('builds record query params without empty filters', () => {
    expect(
      buildTokenUsageQueryParams({
        page: 2,
        pageSize: 5,
        featureType: 'all',
        startAt: null,
        endAt: '2026-04-30T10:00:00.000Z',
      }),
    ).toEqual({
      page: 2,
      page_size: 5,
      end_at: '2026-04-30T10:00:00.000Z',
    });
  });

  it('calculates stacked bar segment heights', () => {
    expect(
      calculateStackedBarSegments({
        inputTokens: 80,
        outputTokens: 20,
        maxTotalTokens: 200,
      }),
    ).toEqual({
      inputPercent: 40,
      outputPercent: 10,
      totalPercent: 50,
    });
  });
```

- [ ] **步骤 2：运行红灯测试**

运行：

```bash
cd frontend && npm run test -- src/features/token-usage/client/tokenUsage.test.ts
```

预期：FAIL，报错包含缺少这些导出函数。

- [ ] **步骤 3：提交红灯测试**

```bash
git add frontend/src/features/token-usage/client/tokenUsage.test.ts
git commit -m "test(token): 覆盖分页筛选前端工具"
```

---

### 任务 6：前端 API 类型与工具实现

**文件：**
- 修改：`frontend/src/types/index.ts`
- 修改：`frontend/src/lib/api/tokenUsage.ts`
- 修改：`frontend/src/features/token-usage/client/tokenUsage.ts`
- 测试：`frontend/src/features/token-usage/client/tokenUsage.test.ts`

- [ ] **步骤 1：扩展前端 DTO 类型**

在 `frontend/src/types/index.ts` 中新增：

```typescript
export type TokenUsageRecordFeatureFilterDTO =
  | 'all'
  | TokenUsageRecordFeatureTypeDTO;
export type TokenUsageChartPresetDTO =
  | 'last_6_hours'
  | 'last_24_hours'
  | 'last_7_days'
  | 'custom';
export type TokenUsageChartGranularityDTO = 'hour' | 'day';

export interface TokenUsagePaginationDTO {
  page: number;
  page_size: number;
  total_records: number;
  total_pages: number;
}
```

修改 `TokenUsageRecordListDTO`：

```typescript
export interface TokenUsageRecordListDTO {
  records: TokenUsageRecordDTO[];
  summary: TokenUsageSummaryDTO;
  pagination: TokenUsagePaginationDTO;
}
```

新增 chart DTO：

```typescript
export interface TokenUsageChartBucketDTO {
  bucket_start: string;
  bucket_label: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface TokenUsageChartDTO {
  preset: TokenUsageChartPresetDTO;
  granularity: TokenUsageChartGranularityDTO;
  range_start: string;
  range_end: string;
  buckets: TokenUsageChartBucketDTO[];
}
```

- [ ] **步骤 2：扩展 API client**

将 `frontend/src/lib/api/tokenUsage.ts` 改为：

```typescript
import { apiFetch } from '@/lib/api/client';
import {
  buildTokenUsageChartQueryParams,
  buildTokenUsageQueryParams,
  type TokenUsageChartQuery,
  type TokenUsageRecordQuery,
} from '@/features/token-usage/client/tokenUsage';
import type { TokenUsageChartDTO, TokenUsageRecordListDTO } from '@/types';

export const listTokenUsageRecords = (query: TokenUsageRecordQuery = {}) =>
  apiFetch<TokenUsageRecordListDTO>(
    '/api/token-usage/records',
    undefined,
    buildTokenUsageQueryParams({
      page: query.page ?? 1,
      pageSize: query.pageSize ?? 5,
      featureType: query.featureType ?? 'all',
      startAt: query.startAt ?? null,
      endAt: query.endAt ?? null,
    }),
  );

export const getTokenUsageChart = (query: TokenUsageChartQuery = {}) =>
  apiFetch<TokenUsageChartDTO>(
    '/api/token-usage/chart',
    undefined,
    buildTokenUsageChartQueryParams({
      featureType: query.featureType ?? 'all',
      preset: query.preset ?? 'last_24_hours',
      startAt: query.startAt ?? null,
      endAt: query.endAt ?? null,
    }),
  );
```

- [ ] **步骤 3：实现前端工具函数**

在 `frontend/src/features/token-usage/client/tokenUsage.ts` 中新增：

```typescript
import type {
  TokenUsageChartPresetDTO,
  TokenUsageRecordFeatureFilterDTO,
} from '@/types';

export interface TokenUsageRecordQuery {
  page?: number;
  pageSize?: number;
  featureType?: TokenUsageRecordFeatureFilterDTO;
  startAt?: string | null;
  endAt?: string | null;
}

export interface TokenUsageChartQuery {
  featureType?: TokenUsageRecordFeatureFilterDTO;
  preset?: TokenUsageChartPresetDTO;
  startAt?: string | null;
  endAt?: string | null;
}

export const isValidPageJump = (page: number, totalPages: number): boolean =>
  Number.isInteger(page) && page >= 1 && page <= Math.max(totalPages, 1);

export const parseDateTimeLocalValue = (value: string): string | null => {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
};

export const formatDateTimeLocalValue = (value: string | null): string => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `${year}-${month}-${day}T${hours}:${minutes}`;
};

export const buildTokenUsageQueryParams = ({
  page,
  pageSize,
  featureType,
  startAt,
  endAt,
}: Required<TokenUsageRecordQuery>) => ({
  page,
  page_size: pageSize,
  ...(featureType !== 'all' ? { feature_type: featureType } : {}),
  ...(startAt ? { start_at: startAt } : {}),
  ...(endAt ? { end_at: endAt } : {}),
});

export const buildTokenUsageChartQueryParams = ({
  featureType,
  preset,
  startAt,
  endAt,
}: Required<TokenUsageChartQuery>) => ({
  preset,
  ...(featureType !== 'all' ? { feature_type: featureType } : {}),
  ...(preset === 'custom' && startAt ? { start_at: startAt } : {}),
  ...(preset === 'custom' && endAt ? { end_at: endAt } : {}),
});

export const calculateStackedBarSegments = ({
  inputTokens,
  outputTokens,
  maxTotalTokens,
}: {
  inputTokens: number;
  outputTokens: number;
  maxTotalTokens: number;
}) => {
  if (maxTotalTokens <= 0) {
    return { inputPercent: 0, outputPercent: 0, totalPercent: 0 };
  }
  const inputPercent = Math.round((inputTokens / maxTotalTokens) * 100);
  const outputPercent = Math.round((outputTokens / maxTotalTokens) * 100);
  return {
    inputPercent,
    outputPercent,
    totalPercent: inputPercent + outputPercent,
  };
};
```

- [ ] **步骤 4：修复已有卡片测试 mock 数据**

在 `frontend/test/TokenUsageCenterCard.test.tsx` 的 mock 响应中加入：

```typescript
pagination: {
  page: 1,
  page_size: 5,
  total_records: 1,
  total_pages: 1,
},
```

并把断言改为：

```typescript
expect(mockedListTokenUsageRecords).toHaveBeenCalledWith({
  page: 1,
  pageSize: 5,
  featureType: 'all',
  startAt: null,
  endAt: null,
});
```

- [ ] **步骤 5：运行前端工具测试**

运行：

```bash
cd frontend && npm run test -- src/features/token-usage/client/tokenUsage.test.ts
```

预期：PASS。

- [ ] **步骤 6：提交实现**

```bash
git add frontend/src/types/index.ts frontend/src/lib/api/tokenUsage.ts frontend/src/features/token-usage/client/tokenUsage.ts frontend/src/features/token-usage/client/tokenUsage.test.ts frontend/test/TokenUsageCenterCard.test.tsx
git commit -m "feat(token): 添加分页筛选前端工具"
```

---

### 任务 7：前端卡片交互红灯测试

**文件：**
- 修改：`frontend/test/TokenUsageCenterCard.test.tsx`

- [ ] **步骤 1：扩展 API mock**

在测试文件顶部新增：

```typescript
const mockedGetTokenUsageChart = vi.hoisted(() => vi.fn());
```

修改 mock：

```typescript
vi.mock("@/lib/api/tokenUsage", () => ({
  listTokenUsageRecords: mockedListTokenUsageRecords,
  getTokenUsageChart: mockedGetTokenUsageChart,
}));
```

在 `beforeEach` 中加入：

```typescript
mockedGetTokenUsageChart.mockReset();
mockedGetTokenUsageChart.mockResolvedValue({
  preset: "last_24_hours",
  granularity: "hour",
  range_start: "2026-04-29T10:00:00Z",
  range_end: "2026-04-30T10:00:00Z",
  buckets: [
    {
      bucket_start: "2026-04-30T10:00:00Z",
      bucket_label: "10:00",
      input_tokens: 200,
      output_tokens: 30,
      total_tokens: 230,
    },
  ],
});
```

- [ ] **步骤 2：新增筛选请求测试**

新增：

```typescript
  it("filters records and chart by feature type", async () => {
    mockedListTokenUsageRecords.mockResolvedValue(createRecordListResult());
    render(<TokenUsageCenterCard />);

    fireEvent.click(screen.getByRole("button", { name: /Token 消耗记录中心/ }));
    await waitFor(() => expect(mockedListTokenUsageRecords).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("功能筛选"), {
      target: { value: "match_analysis" },
    });
    fireEvent.click(screen.getByRole("button", { name: "查询" }));

    await waitFor(() =>
      expect(mockedListTokenUsageRecords).toHaveBeenLastCalledWith({
        page: 1,
        pageSize: 5,
        featureType: "match_analysis",
        startAt: null,
        endAt: null,
      }),
    );
    expect(mockedGetTokenUsageChart).toHaveBeenLastCalledWith({
      featureType: "match_analysis",
      preset: "last_24_hours",
      startAt: null,
      endAt: null,
    });
  });
```

- [ ] **步骤 3：新增页号跳转测试**

新增：

```typescript
  it("jumps to an entered page", async () => {
    mockedListTokenUsageRecords.mockResolvedValue(
      createRecordListResult({
        pagination: {
          page: 1,
          page_size: 5,
          total_records: 12,
          total_pages: 3,
        },
      }),
    );
    render(<TokenUsageCenterCard />);

    fireEvent.click(screen.getByRole("button", { name: /Token 消耗记录中心/ }));
    await waitFor(() => expect(screen.getByText("第 1 / 3 页")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("跳转页号"), {
      target: { value: "3" },
    });
    fireEvent.click(screen.getByRole("button", { name: "跳转" }));

    await waitFor(() =>
      expect(mockedListTokenUsageRecords).toHaveBeenLastCalledWith({
        page: 3,
        pageSize: 5,
        featureType: "all",
        startAt: null,
        endAt: null,
      }),
    );
  });
```

- [ ] **步骤 4：新增图表渲染测试**

新增：

```typescript
  it("renders stacked chart buckets", async () => {
    mockedListTokenUsageRecords.mockResolvedValue(createRecordListResult());
    render(<TokenUsageCenterCard />);

    fireEvent.click(screen.getByRole("button", { name: /Token 消耗记录中心/ }));

    await waitFor(() => expect(screen.getByText("输入 / 输出趋势")).toBeInTheDocument());
    expect(screen.getByText("10:00")).toBeInTheDocument();
    expect(screen.getByLabelText("10:00 输入 200 输出 30")).toBeInTheDocument();
  });
```

- [ ] **步骤 5：添加测试 helper**

在测试文件底部新增：

```typescript
function createRecordListResult(overrides: Partial<TokenUsageRecordListDTO> = {}) {
  return {
    records: [
      {
        id: "match_analysis:1",
        feature_type: "match_analysis",
        feature_label: "匹配分析",
        title: "李老师 - 匹配分析",
        input_tokens: 200,
        output_tokens: 30,
        cached_tokens: 80,
        total_tokens: 230,
        model_name: "gpt-test",
        identity_name: "博士申请邮箱",
        created_at: "2026-04-29T10:00:00Z",
        status: "success",
      },
    ],
    summary: {
      input_tokens: 200,
      output_tokens: 30,
      cached_tokens: 80,
      total_tokens: 230,
      record_count: 1,
    },
    pagination: {
      page: 1,
      page_size: 5,
      total_records: 1,
      total_pages: 1,
    },
    ...overrides,
  };
}
```

需要从 `@/types` 导入 `TokenUsageRecordListDTO`。

- [ ] **步骤 6：运行红灯测试**

运行：

```bash
cd frontend && npm run test -- test/TokenUsageCenterCard.test.tsx
```

预期：FAIL，报错包含找不到 `功能筛选`、`跳转页号` 或 `输入 / 输出趋势`。

- [ ] **步骤 7：提交红灯测试**

```bash
git add frontend/test/TokenUsageCenterCard.test.tsx
git commit -m "test(token): 覆盖记录中心筛选分页图表"
```

---

### 任务 8：前端卡片交互和图表实现

**文件：**
- 修改：`frontend/src/components/molecules/TokenUsageCenterCard.tsx`
- 测试：`frontend/test/TokenUsageCenterCard.test.tsx`

- [ ] **步骤 1：改造组件状态和加载函数**

在 `TokenUsageCenterCard` 中新增状态：

```tsx
const [featureType, setFeatureType] = useState<TokenUsageRecordFeatureFilterDTO>('all');
const [startAt, setStartAt] = useState<string | null>(null);
const [endAt, setEndAt] = useState<string | null>(null);
const [page, setPage] = useState(1);
const [pageInput, setPageInput] = useState('1');
const [chartPreset, setChartPreset] = useState<TokenUsageChartPresetDTO>('last_24_hours');
const [chart, setChart] = useState<TokenUsageChartDTO | null>(null);
const [chartError, setChartError] = useState<string | null>(null);
```

把 `loadRecords` 改为接收 query：

```tsx
const loadRecords = useCallback(
  async (nextPage = page) => {
    setLoading(true);
    setError(null);
    try {
      const nextResult = await listTokenUsageRecords({
        page: nextPage,
        pageSize: 5,
        featureType,
        startAt,
        endAt,
      });
      setResult(nextResult);
      setPage(nextResult.pagination.page);
      setPageInput(String(nextResult.pagination.page));
      setLoaded(true);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '加载 token 消耗记录失败');
    } finally {
      setLoading(false);
    }
  },
  [endAt, featureType, page, startAt],
);
```

新增 chart 加载：

```tsx
const loadChart = useCallback(async () => {
  setChartError(null);
  try {
    setChart(
      await getTokenUsageChart({
        featureType,
        preset: chartPreset,
        startAt,
        endAt,
      }),
    );
  } catch (loadError) {
    setChartError(loadError instanceof Error ? loadError.message : '加载趋势图失败');
  }
}, [chartPreset, endAt, featureType, startAt]);
```

- [ ] **步骤 2：新增筛选表单小组件**

在同文件内新增：

```tsx
function TokenUsageFilters({
  featureType,
  startAt,
  endAt,
  onFeatureTypeChange,
  onStartAtChange,
  onEndAtChange,
  onSubmit,
  onReset,
}: {
  featureType: TokenUsageRecordFeatureFilterDTO;
  startAt: string | null;
  endAt: string | null;
  onFeatureTypeChange: (value: TokenUsageRecordFeatureFilterDTO) => void;
  onStartAtChange: (value: string | null) => void;
  onEndAtChange: (value: string | null) => void;
  onSubmit: () => void;
  onReset: () => void;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-[1fr_1fr_1fr_auto]">
      <label className="block">
        <span className="mb-2 block text-xs font-medium text-stone-500">功能筛选</span>
        <select
          aria-label="功能筛选"
          value={featureType}
          onChange={(event) =>
            onFeatureTypeChange(event.target.value as TokenUsageRecordFeatureFilterDTO)
          }
          className="w-full rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700"
        >
          <option value="all">全部</option>
          <option value="crawl">智能爬取</option>
          <option value="match_analysis">匹配分析</option>
          <option value="draft_generation">AI 草稿</option>
        </select>
      </label>
      <label className="block">
        <span className="mb-2 block text-xs font-medium text-stone-500">开始时间</span>
        <input
          type="datetime-local"
          value={formatDateTimeLocalValue(startAt)}
          onChange={(event) => onStartAtChange(parseDateTimeLocalValue(event.target.value))}
          className="w-full rounded-xl border border-stone-200 px-3 py-2 text-sm text-stone-700"
        />
      </label>
      <label className="block">
        <span className="mb-2 block text-xs font-medium text-stone-500">结束时间</span>
        <input
          type="datetime-local"
          value={formatDateTimeLocalValue(endAt)}
          onChange={(event) => onEndAtChange(parseDateTimeLocalValue(event.target.value))}
          className="w-full rounded-xl border border-stone-200 px-3 py-2 text-sm text-stone-700"
        />
      </label>
      <div className="flex items-end gap-2">
        <button type="button" onClick={onSubmit} className="ui-btn-primary">
          查询
        </button>
        <button type="button" onClick={onReset} className="ui-btn-secondary">
          重置
        </button>
      </div>
    </div>
  );
}
```

- [ ] **步骤 3：新增分页小组件**

新增：

```tsx
function TokenUsagePagination({
  page,
  totalPages,
  pageInput,
  onPageInputChange,
  onPageChange,
  onJump,
}: {
  page: number;
  totalPages: number;
  pageInput: string;
  onPageInputChange: (value: string) => void;
  onPageChange: (page: number) => void;
  onJump: () => void;
}) {
  if (totalPages <= 0) return null;
  return (
    <div className="flex flex-wrap items-center justify-end gap-2 text-sm text-stone-600">
      <button
        type="button"
        className="ui-btn-secondary px-3 py-1.5 text-sm"
        disabled={page <= 1}
        onClick={() => onPageChange(page - 1)}
      >
        上一页
      </button>
      <span>第 {page} / {totalPages} 页</span>
      <input
        aria-label="跳转页号"
        type="number"
        min={1}
        max={totalPages}
        value={pageInput}
        onChange={(event) => onPageInputChange(event.target.value)}
        className="w-20 rounded-xl border border-stone-200 px-3 py-1.5 text-sm"
      />
      <button type="button" className="ui-btn-primary px-3 py-1.5 text-sm" onClick={onJump}>
        跳转
      </button>
      <button
        type="button"
        className="ui-btn-secondary px-3 py-1.5 text-sm"
        disabled={page >= totalPages}
        onClick={() => onPageChange(page + 1)}
      >
        下一页
      </button>
    </div>
  );
}
```

- [ ] **步骤 4：新增趋势图小组件**

新增：

```tsx
function TokenUsageTrendChart({
  chart,
  preset,
  onPresetChange,
  error,
  onRetry,
}: {
  chart: TokenUsageChartDTO | null;
  preset: TokenUsageChartPresetDTO;
  onPresetChange: (value: TokenUsageChartPresetDTO) => void;
  error: string | null;
  onRetry: () => void;
}) {
  const maxTotal = Math.max(...(chart?.buckets.map((bucket) => bucket.total_tokens) ?? [0]), 0);
  return (
    <section className="border-t border-stone-200 pt-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-base font-semibold text-stone-900">输入 / 输出趋势</h3>
        <div className="flex flex-wrap gap-2">
          {[
            ['last_6_hours', '最近 6 小时'],
            ['last_24_hours', '最近 24 小时'],
            ['last_7_days', '最近 7 天'],
            ['custom', '自定义范围'],
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => onPresetChange(value as TokenUsageChartPresetDTO)}
              className={clsx(
                'rounded-xl border px-3 py-1.5 text-xs font-medium',
                preset === value
                  ? 'border-primary bg-primary text-white'
                  : 'border-stone-200 bg-white text-stone-600',
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {error ? (
        <div className="mt-3 rounded-2xl border border-red-200 bg-red-50 px-4 py-4 text-sm text-red-700">
          {error}
          <button type="button" className="ml-3 underline" onClick={onRetry}>
            重试
          </button>
        </div>
      ) : chart === null || chart.buckets.length === 0 || maxTotal === 0 ? (
        <div className="mt-3 rounded-2xl border border-stone-200 bg-stone-50 px-4 py-8 text-center text-sm text-stone-500">
          暂无趋势数据
        </div>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-2xl border border-stone-200 bg-[#fcfbf8] px-4 py-4">
          <div className="flex min-w-max items-end gap-3">
            {chart.buckets.map((bucket) => {
              const segments = calculateStackedBarSegments({
                inputTokens: bucket.input_tokens,
                outputTokens: bucket.output_tokens,
                maxTotalTokens: maxTotal,
              });
              return (
                <div key={bucket.bucket_start} className="flex w-12 flex-col items-center gap-2">
                  <div
                    aria-label={`${bucket.bucket_label} 输入 ${bucket.input_tokens} 输出 ${bucket.output_tokens}`}
                    className="flex h-36 w-7 items-end overflow-hidden rounded-t-lg bg-stone-200"
                    title={`输入 ${bucket.input_tokens.toLocaleString('zh-CN')} / 输出 ${bucket.output_tokens.toLocaleString('zh-CN')}`}
                  >
                    <div style={{ height: `${segments.totalPercent}%` }} className="flex w-full flex-col justify-end">
                      <div style={{ height: `${segments.outputPercent}%` }} className="bg-sky-500" />
                      <div style={{ height: `${segments.inputPercent}%` }} className="bg-emerald-500" />
                    </div>
                  </div>
                  <span className="text-[11px] text-stone-500">{bucket.bucket_label}</span>
                </div>
              );
            })}
            <div className="ml-2 self-start text-xs leading-6 text-stone-500">
              <div><span className="text-emerald-600">■</span> 输入</div>
              <div><span className="text-sky-600">■</span> 输出</div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
```

- [ ] **步骤 5：接入筛选、分页和图表**

在主渲染中：

- 在 summary 前渲染 `TokenUsageFilters`。
- 在列表后渲染 `TokenUsagePagination`。
- 在分页后渲染 `TokenUsageTrendChart`。
- `onSubmit` 调用 `setPage(1)` 后 `loadRecords(1)` 和 `loadChart()`。
- `onReset` 把功能设为 `all`，时间设为 `null`，preset 设为 `last_24_hours`，再请求第一页和默认图表。
- `onJump` 解析 `pageInput`，通过 `isValidPageJump` 后调用 `loadRecords(targetPage)`。
- `onPresetChange` 设置 preset 后请求图表。

- [ ] **步骤 6：运行卡片测试**

运行：

```bash
cd frontend && npm run test -- test/TokenUsageCenterCard.test.tsx
```

预期：PASS。

- [ ] **步骤 7：运行前端构建**

运行：

```bash
cd frontend && npm run build
```

预期：PASS。

- [ ] **步骤 8：提交实现**

```bash
git add frontend/src/components/molecules/TokenUsageCenterCard.tsx frontend/test/TokenUsageCenterCard.test.tsx
git commit -m "feat(token): 实现记录中心分页筛选和趋势图"
```

---

### 任务 9：最终验证和收尾

**文件：**
- 检查：全部本次变更文件

- [ ] **步骤 1：运行后端 token 测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_token_usage_records
```

预期：PASS。

- [ ] **步骤 2：运行后端相关测试**

运行：

```bash
cd backend && uv run python -m unittest test.test_token_usage_records test.test_match_analysis_runtime test.test_crawl_job_metrics test.test_crawl_jobs_api
```

预期：PASS。

- [ ] **步骤 3：运行前端 token 测试**

运行：

```bash
cd frontend && npm run test -- src/features/token-usage/client/tokenUsage.test.ts test/TokenUsageCenterCard.test.tsx
```

预期：PASS。

- [ ] **步骤 4：运行前端 lint**

运行：

```bash
cd frontend && npm run lint
```

预期：PASS。

- [ ] **步骤 5：运行前端 build**

运行：

```bash
cd frontend && npm run build
```

预期：PASS。允许保留现有 Vite chunk size warning。

- [ ] **步骤 6：检查工作区状态和 diff**

运行：

```bash
git status --short
git diff --stat
git log --oneline -8
```

预期：

- `git status --short` 只显示本功能相关文件，或为空。
- diff 只涉及计划列出的文件。
- 最近提交按任务拆分清晰。

- [ ] **步骤 7：提交验证中产生的修正**

如果验证中产生必要修正，提交：

```bash
git add backend/app/schemas/token_usage.py backend/app/services/token_usage_records.py backend/app/api/token_usage.py backend/test/test_token_usage_records.py frontend/src/types/index.ts frontend/src/lib/api/tokenUsage.ts frontend/src/features/token-usage/client/tokenUsage.ts frontend/src/features/token-usage/client/tokenUsage.test.ts frontend/src/components/molecules/TokenUsageCenterCard.tsx frontend/test/TokenUsageCenterCard.test.tsx
git commit -m "fix(token): 修正历史分页趋势图验证问题"
```

如果没有新增修正，跳过此步骤。

---

## 自检清单

- 规格中的分页、功能筛选、日期小时范围筛选都有后端和前端任务覆盖。
- 图表 preset、自定义范围自动粒度、堆叠柱都有任务覆盖。
- 计划不引入新的统一 token 表。
- 计划没有改变 token 采集来源。
- 后端 API 层只做参数校验和 service 调用。
- 前端卡片拆出小组件，避免把所有逻辑堆在一个长 JSX 块中。
- 每个实现任务都有红灯测试、绿灯验证和 commit 步骤。
