from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.modules.identities.profiles.api import delete_identity
from app.models import (
    AppSetting,
    Base,
    EmailTask,
    IdentityCommunicationGroup,
    IdentityMaterial,
    IdentityProfile,
    IdentityProfessorMatchResult,
    LLMProfile,
    MatchAnalysisJob,
    MatchAnalysisJobItem,
    MatchAnalysisJobItemStatus,
    MatchAnalysisRun,
    Professor,
)
from app.services import llm_runtime, task_runtime
from app.services.match_results import (
    load_resolved_match_result,
    match_result_is_stale,
)
from app.services.match_analysis_job_runtime import (
    serialize_match_analysis_job,
    serialize_match_analysis_job_item,
)
from app.modules.identities.public import delete_identity_material_record
from app.services.task_runtime import (
    MatchAnalysisAlreadyRunningError,
    calculate_task_match_once,
    recover_interrupted_match_analysis_runs,
)


class MatchAnalysisRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "match_analysis_test.db"
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_path.as_posix()}",
            future=True,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )
        self._run_async(self._create_schema())
        self.email_task_id = self._run_async(self._create_email_task())
        self.runtime_adaptation_patcher = patch(
            "app.services.task_runtime.llm_runtime.ensure_llm_runtime_adaptation",
            new=AsyncMock(return_value=llm_runtime.LLMRuntimeAdaptation("chat_completions", None)),
        )
        self.runtime_adaptation_patcher.start()

    def tearDown(self) -> None:
        self.runtime_adaptation_patcher.stop()
        self._run_async(self.engine.dispose())
        self.temp_dir.cleanup()

    def _run_async(self, awaitable):
        return asyncio.run(awaitable)

    async def _create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def _create_email_task(self) -> int:
        async with self.session_factory() as session:
            identity = IdentityProfile(
                name="测试身份",
                profile_name="测试身份",
                sender_name="测试身份",
                email_address="sender@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="llm",
            )
            profile = LLMProfile(
                name="测试模型",
                provider="openai",
                api_key="test-key",
                model_name="gpt-test",
            )
            professor = Professor(
                name="李老师",
                email="prof@example.edu",
                title="Professor",
                university="Example University",
                school="Computer Science",
                research_direction="Information Extraction",
                recent_papers=["Paper A"],
            )
            session.add_all([identity, profile, professor])
            await session.flush()

            material = IdentityMaterial(
                identity_id=identity.id,
                display_name="简历",
                file_path="data/materials/resume.txt",
                original_filename="resume.txt",
                material_type="resume",
                sha256="a" * 64,
                extracted_text="我做过信息抽取与智能体相关研究。",
            )
            session.add(material)
            await session.flush()
            identity.current_primary_material_id = material.id

            task = EmailTask(
                identity_id=identity.id,
                llm_profile_id=profile.id,
                professor_id=professor.id,
                primary_material_id=material.id,
                selected_material_ids=[],
            )
            session.add(task)
            await session.commit()
            return task.id

    def test_calculate_match_persists_successful_token_audit(self) -> None:
        generation = llm_runtime.GeneratedMatchEvaluation(
            result=llm_runtime.MatchEvaluationResult(
                match_score=91,
                match_reason="研究方向接近",
                fit_points=["信息抽取"],
                risk_points=[],
                keywords=["信息抽取"],
            ),
            usage=llm_runtime.ChatCompletionUsage(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                cached_tokens=64,
            ),
            endpoint_kind="chat_completions",
            status_code=200,
            duration_ms=321,
            prompt_hash="a" * 64,
            stable_prefix_hash="b" * 64,
        )

        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            new=AsyncMock(return_value=generation),
        ):
            result = self._run_async(
                calculate_task_match_once(
                    self.session_factory,
                    self.email_task_id,
                ),
            )

        self.assertEqual(result.usage.total_tokens, 120)
        self.assertIsNotNone(result.run_id)

        runs = self._run_async(self._list_runs())
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0].success)
        self.assertEqual(runs[0].status, "succeeded")
        self.assertEqual(runs[0].match_score, 91)
        self.assertEqual(runs[0].match_reason, "研究方向接近")
        self.assertEqual(runs[0].fit_points, ["信息抽取"])
        self.assertEqual(runs[0].risk_points, [])
        self.assertEqual(runs[0].match_keywords, ["信息抽取"])
        self.assertEqual(runs[0].cached_tokens, 64)
        self.assertIsNotNone(runs[0].started_at)
        self.assertIsNotNone(runs[0].finished_at)

        canonical_results = self._run_async(self._list_canonical_results())
        self.assertEqual(len(canonical_results), 1)
        self.assertEqual(canonical_results[0].match_score, 91)
        self.assertEqual(canonical_results[0].match_reason, "研究方向接近")
        self.assertEqual(canonical_results[0].latest_analysis_run_id, runs[0].id)

    def test_recalculation_overwrites_one_canonical_identity_professor_result(self) -> None:
        generations = [
            llm_runtime.GeneratedMatchEvaluation(
                result=llm_runtime.MatchEvaluationResult(
                    match_score=91,
                    match_reason="第一次分析",
                    fit_points=["信息抽取"],
                    risk_points=[],
                    keywords=["IE"],
                ),
                usage=None,
            ),
            llm_runtime.GeneratedMatchEvaluation(
                result=llm_runtime.MatchEvaluationResult(
                    match_score=74,
                    match_reason="材料更新后的分析",
                    fit_points=["智能体"],
                    risk_points=["论文证据有限"],
                    keywords=["agent"],
                ),
                usage=None,
            ),
        ]

        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            new=AsyncMock(side_effect=generations),
        ):
            first = self._run_async(
                calculate_task_match_once(self.session_factory, self.email_task_id),
            )
            second = self._run_async(
                calculate_task_match_once(self.session_factory, self.email_task_id),
            )

        canonical_results = self._run_async(self._list_canonical_results())
        self.assertEqual(len(canonical_results), 1)
        self.assertEqual(canonical_results[0].match_score, 74)
        self.assertEqual(canonical_results[0].match_reason, "材料更新后的分析")
        self.assertEqual(canonical_results[0].fit_points, ["智能体"])
        self.assertEqual(canonical_results[0].risk_points, ["论文证据有限"])
        self.assertEqual(canonical_results[0].latest_analysis_run_id, second.run_id)
        self.assertNotEqual(first.run_id, second.run_id)

    def test_job_item_serializer_preserves_its_own_historical_score(self) -> None:
        async def scenario() -> list[int | None]:
            async with self.session_factory() as session:
                task = await session.get(EmailTask, self.email_task_id)
                assert task is not None
                task.match_score = 88

                run = MatchAnalysisRun(
                    email_task_id=task.id,
                    professor_id=task.professor_id,
                    identity_id=task.identity_id,
                    llm_profile_id=task.llm_profile_id,
                    primary_material_id=task.primary_material_id,
                    status="succeeded",
                    success=True,
                    match_score=41,
                )
                job = MatchAnalysisJob(
                    name="历史匹配任务",
                    identity_id=task.identity_id,
                    match_source_identity_id=task.identity_id,
                    llm_profile_id=task.llm_profile_id,
                    status="completed",
                    target_count=3,
                    succeeded_count=2,
                )
                session.add_all([run, job])
                await session.flush()

                session.add_all(
                    [
                        MatchAnalysisJobItem(
                            job_id=job.id,
                            professor_id=task.professor_id,
                            email_task_id=task.id,
                            match_analysis_run_id=run.id,
                            status=MatchAnalysisJobItemStatus.SUCCEEDED.value,
                        ),
                        MatchAnalysisJobItem(
                            job_id=job.id,
                            professor_id=task.professor_id,
                            email_task_id=task.id,
                            status=MatchAnalysisJobItemStatus.QUEUED.value,
                        ),
                        MatchAnalysisJobItem(
                            job_id=job.id,
                            professor_id=task.professor_id,
                            email_task_id=task.id,
                            status=MatchAnalysisJobItemStatus.SUCCEEDED.value,
                        ),
                    ],
                )
                await session.commit()

                items = list(
                    await session.scalars(
                        select(MatchAnalysisJobItem)
                        .options(
                            selectinload(MatchAnalysisJobItem.professor),
                            selectinload(MatchAnalysisJobItem.email_task),
                            selectinload(MatchAnalysisJobItem.match_analysis_run),
                        )
                        .where(MatchAnalysisJobItem.job_id == job.id)
                        .order_by(MatchAnalysisJobItem.id.asc()),
                    ),
                )
                return [
                    serialize_match_analysis_job_item(item).match_score
                    for item in items
                ]

        self.assertEqual(self._run_async(scenario()), [41, None, 88])

    def test_legacy_result_reader_tolerates_malformed_json_columns(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                task = await session.get(EmailTask, self.email_task_id)
                assert task is not None
                connection = await session.connection()
                await connection.exec_driver_sql(
                    """
                    UPDATE email_tasks
                    SET match_score = 67,
                        match_reason = '异常 JSON 的旧结果',
                        fit_points = 'not-json',
                        risk_points = '{"unexpected": true}',
                        match_keywords = '["可用关键词", 2]'
                    WHERE id = ?
                    """,
                    (task.id,),
                )
                await session.commit()
                _, result = await load_resolved_match_result(
                    session,
                    active_identity_id=task.identity_id,
                    professor_id=task.professor_id,
                )
                return result

        result = self._run_async(scenario())
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result.legacy_task_snapshot)
        self.assertEqual(result.match_score, 67)
        self.assertEqual(result.fit_points, ())
        self.assertEqual(result.risk_points, ())
        self.assertEqual(result.match_keywords, ("可用关键词",))

    def test_canonical_result_reader_tolerates_malformed_json_columns(self) -> None:
        async def scenario():
            async with self.session_factory() as session:
                task = await session.get(EmailTask, self.email_task_id)
                assert task is not None
                canonical = IdentityProfessorMatchResult(
                    identity_id=task.identity_id,
                    professor_id=task.professor_id,
                    llm_profile_id=task.llm_profile_id,
                    source_email_task_id=task.id,
                    match_score=79,
                    match_reason="异常 JSON 的当前结果",
                    fit_points=[],
                    risk_points=[],
                    match_keywords=[],
                )
                session.add(canonical)
                await session.commit()
                connection = await session.connection()
                await connection.exec_driver_sql(
                    """
                    UPDATE identity_professor_match_results
                    SET fit_points = 'not-json',
                        risk_points = '{"unexpected": true}',
                        match_keywords = '["当前关键词", 2]'
                    WHERE id = ?
                    """,
                    (canonical.id,),
                )
                await session.commit()
                _, result = await load_resolved_match_result(
                    session,
                    active_identity_id=task.identity_id,
                    professor_id=task.professor_id,
                )
                return result

        result = self._run_async(scenario())
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.legacy_task_snapshot)
        self.assertEqual(result.match_score, 79)
        self.assertEqual(result.fit_points, ())
        self.assertEqual(result.risk_points, ())
        self.assertEqual(result.match_keywords, ("当前关键词",))

    def test_shared_group_analysis_uses_source_identity_and_stores_result_under_source(self) -> None:
        (
            source_identity_id,
            active_identity_id,
            source_material_id,
        ) = self._run_async(self._configure_shared_match_source())
        generation = llm_runtime.GeneratedMatchEvaluation(
            result=llm_runtime.MatchEvaluationResult(
                match_score=93,
                match_reason="依据身份 A 的材料与导师方向高度匹配",
                fit_points=["信息抽取"],
                risk_points=["需补充论文"],
                keywords=["IE"],
            ),
            usage=None,
        )

        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            new=AsyncMock(return_value=generation),
        ) as mocked_generate:
            action_result = self._run_async(
                calculate_task_match_once(self.session_factory, self.email_task_id),
            )

        self.assertEqual(action_result.identity_id, active_identity_id)
        self.assertEqual(action_result.match_source_identity_id, source_identity_id)
        self.assertEqual(
            mocked_generate.await_args.kwargs["identity"].id,
            source_identity_id,
        )
        self.assertEqual(
            mocked_generate.await_args.kwargs["primary_material"].id,
            source_material_id,
        )
        self.assertTrue(
            all(
                material.identity_id == source_identity_id
                for material in mocked_generate.await_args.kwargs["available_materials"]
            ),
        )

        state = self._run_async(
            self._load_shared_match_state(active_identity_id),
        )
        self.assertEqual(len(state["canonical_results"]), 1)
        self.assertEqual(
            state["canonical_results"][0].identity_id,
            source_identity_id,
        )
        self.assertEqual(state["canonical_results"][0].match_score, 93)
        self.assertEqual(state["task"].identity_id, active_identity_id)
        self.assertEqual(
            state["task"].match_source_identity_id,
            source_identity_id,
        )
        self.assertEqual(state["task"].match_score, 93)
        self.assertEqual(state["runs"][0].identity_id, source_identity_id)
        self.assertEqual(state["resolved_scope_source_id"], source_identity_id)
        self.assertEqual(state["resolved_match_reason"], generation.result.match_reason)
        self.assertIsNone(
            self._run_async(
                self._clear_shared_source_and_load_match(active_identity_id),
            ),
        )

    def test_deleting_shared_match_source_removes_canonical_result_and_runs(self) -> None:
        source_identity_id, active_identity_id, _ = self._run_async(
            self._configure_shared_match_source(),
        )
        generation = llm_runtime.GeneratedMatchEvaluation(
            result=llm_runtime.MatchEvaluationResult(
                match_score=93,
                match_reason="删除依据身份前的共享结果",
                fit_points=["信息抽取"],
                risk_points=[],
                keywords=["IE"],
            ),
            usage=None,
        )

        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            new=AsyncMock(return_value=generation),
        ):
            self._run_async(
                calculate_task_match_once(self.session_factory, self.email_task_id),
            )
        self._run_async(
            self._link_latest_run_to_shared_match_job(
                source_identity_id,
                active_identity_id,
            ),
        )

        state = self._run_async(
            self._delete_shared_match_source_and_load_state(
                source_identity_id,
                active_identity_id,
            ),
        )

        self.assertIsNone(state["source_identity"])
        self.assertIsNone(state["active_communication_group_id"])
        self.assertEqual(state["canonical_results"], [])
        self.assertEqual(state["runs"], [])
        self.assertEqual(state["job_match_source_identity_ids"], [None])
        self.assertEqual(state["job_item_run_ids"], [None])
        self.assertEqual(state["task_match_source_identity_id"], source_identity_id)
        self.assertIsNone(state["resolved_match_reason"])

    def test_deleting_active_identity_cleans_shared_task_references_with_foreign_keys(self) -> None:
        source_identity_id, active_identity_id, _ = self._run_async(
            self._configure_shared_match_source(),
        )
        generation = llm_runtime.GeneratedMatchEvaluation(
            result=llm_runtime.MatchEvaluationResult(
                match_score=93,
                match_reason="删除活动身份前的共享结果",
                fit_points=["信息抽取"],
                risk_points=[],
                keywords=["IE"],
            ),
            usage=None,
        )
        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            new=AsyncMock(return_value=generation),
        ):
            self._run_async(
                calculate_task_match_once(self.session_factory, self.email_task_id),
            )
        self._run_async(
            self._link_latest_run_to_shared_match_job(
                source_identity_id,
                active_identity_id,
            ),
        )

        async def scenario() -> dict[str, object]:
            async with self.session_factory() as session:
                connection = await session.connection()
                await connection.exec_driver_sql("PRAGMA foreign_keys = ON")
                await delete_identity(active_identity_id, session=session)
                source_identity = await session.get(IdentityProfile, source_identity_id)
                return {
                    "source_identity": source_identity,
                    "results": list(
                        await session.scalars(select(IdentityProfessorMatchResult)),
                    ),
                    "runs": list(await session.scalars(select(MatchAnalysisRun))),
                    "items": list(
                        await session.scalars(select(MatchAnalysisJobItem)),
                    ),
                    "jobs": list(await session.scalars(select(MatchAnalysisJob))),
                    "tasks": list(await session.scalars(select(EmailTask))),
                }

        state = self._run_async(scenario())
        self.assertIsNotNone(state["source_identity"])
        results = state["results"]
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0].source_email_task_id)
        self.assertIsNone(results[0].latest_analysis_run_id)
        self.assertEqual(state["runs"], [])
        self.assertEqual(state["items"], [])
        self.assertEqual(state["jobs"], [])
        self.assertEqual(state["tasks"], [])

    def test_deleting_match_source_cancels_queued_cross_identity_job(self) -> None:
        source_identity_id, active_identity_id, _ = self._run_async(
            self._configure_shared_match_source(),
        )

        async def scenario() -> tuple[MatchAnalysisJob, MatchAnalysisJobItem]:
            async with self.session_factory() as session:
                task = await session.get(EmailTask, self.email_task_id)
                assert task is not None
                job = MatchAnalysisJob(
                    name="待执行的共享匹配任务",
                    identity_id=active_identity_id,
                    match_source_identity_id=source_identity_id,
                    llm_profile_id=task.llm_profile_id,
                    status="queued",
                    target_count=1,
                )
                session.add(job)
                await session.flush()
                item = MatchAnalysisJobItem(
                    job_id=job.id,
                    professor_id=task.professor_id,
                    email_task_id=task.id,
                    status=MatchAnalysisJobItemStatus.QUEUED.value,
                )
                session.add(item)
                await session.commit()

                connection = await session.connection()
                await connection.exec_driver_sql("PRAGMA foreign_keys = ON")
                await delete_identity(source_identity_id, session=session)
                saved_job = await session.get(MatchAnalysisJob, job.id)
                saved_item = await session.get(MatchAnalysisJobItem, item.id)
                assert saved_job is not None
                assert saved_item is not None
                await session.refresh(saved_job)
                await session.refresh(saved_item)
                return saved_job, saved_item

        job, item = self._run_async(scenario())
        self.assertEqual(job.status, "canceled")
        self.assertIsNone(job.match_source_identity_id)
        self.assertIsNone(serialize_match_analysis_job(job).match_source_identity_id)
        self.assertIsNotNone(job.cancel_requested_at)
        self.assertEqual(job.last_error, "匹配依据身份已删除，任务已取消")
        self.assertEqual(item.status, MatchAnalysisJobItemStatus.CANCELED.value)
        self.assertEqual(item.skip_reason, "匹配依据身份已删除")
        self.assertIsNotNone(item.finished_at)

    def test_deleting_analysis_material_keeps_result_but_marks_it_stale(self) -> None:
        generation = llm_runtime.GeneratedMatchEvaluation(
            result=llm_runtime.MatchEvaluationResult(
                match_score=88,
                match_reason="删除材料前的分析",
                fit_points=["信息抽取"],
                risk_points=[],
                keywords=["IE"],
            ),
            usage=None,
        )
        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            new=AsyncMock(return_value=generation),
        ):
            self._run_async(
                calculate_task_match_once(self.session_factory, self.email_task_id),
            )

        deletion_state = self._run_async(self._delete_match_material())

        self.assertEqual(deletion_state["detached_match_result_count"], 1)
        self.assertIsNone(deletion_state["current_primary_material_id"])
        self.assertIsNone(deletion_state["result_primary_material_id"])
        self.assertTrue(deletion_state["is_stale"])
        self.assertEqual(deletion_state["match_score"], 88)

    def test_calculate_match_passes_workflow_session_and_runtime_adaptation(self) -> None:
        adaptation = llm_runtime.LLMRuntimeAdaptation("responses", {"enable_thinking": False})
        workflow_sessions: list[AsyncSession] = []
        generation = llm_runtime.GeneratedMatchEvaluation(
            result=llm_runtime.MatchEvaluationResult(
                match_score=91,
                match_reason="研究方向接近",
                fit_points=["信息抽取"],
                risk_points=[],
                keywords=["信息抽取"],
            ),
            usage=None,
        )

        async def fake_ensure(session: AsyncSession, _profile: object) -> llm_runtime.LLMRuntimeAdaptation:
            self.assertIsInstance(session, AsyncSession)
            workflow_sessions.append(session)
            return adaptation

        async def fake_generate(**kwargs: object) -> llm_runtime.GeneratedMatchEvaluation:
            self.assertEqual(len(workflow_sessions), 1)
            self.assertIs(kwargs["session"], workflow_sessions[0])
            self.assertIs(kwargs["adaptation"], adaptation)
            return generation

        with (
            patch(
                "app.services.task_runtime.llm_runtime.ensure_llm_runtime_adaptation",
                new=AsyncMock(side_effect=fake_ensure),
            ) as adaptation_mock,
            patch(
                "app.services.task_runtime.llm_runtime.generate_match_evaluation",
                new=AsyncMock(side_effect=fake_generate),
            ) as generate_mock,
        ):
            result = self._run_async(calculate_task_match_once(self.session_factory, self.email_task_id))

        self.assertIsNotNone(result.run_id)
        adaptation_mock.assert_awaited_once()
        generate_mock.assert_awaited_once()

    def test_calculate_match_uses_identity_current_primary_material(self) -> None:
        alt_material_id = self._run_async(self._switch_identity_default_material())
        generation = llm_runtime.GeneratedMatchEvaluation(
            result=llm_runtime.MatchEvaluationResult(
                match_score=89,
                match_reason="当前默认材料更匹配",
                fit_points=["智能体"],
                risk_points=[],
                keywords=["agent"],
            ),
            usage=None,
        )

        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            new=AsyncMock(return_value=generation),
        ) as mocked_generate:
            result = self._run_async(
                calculate_task_match_once(
                    self.session_factory,
                    self.email_task_id,
                ),
            )

        self.assertIsNotNone(result.run_id)
        used_material = mocked_generate.await_args.kwargs["primary_material"]
        self.assertEqual(used_material.id, alt_material_id)
        runs = self._run_async(self._list_runs())
        self.assertEqual(runs[0].primary_material_id, alt_material_id)

    def test_calculate_match_succeeds_when_task_material_cleared(self) -> None:
        current_material_id = self._run_async(self._clear_task_primary_material())
        generation = llm_runtime.GeneratedMatchEvaluation(
            result=llm_runtime.MatchEvaluationResult(
                match_score=87,
                match_reason="使用个人页默认材料",
                fit_points=["信息抽取"],
                risk_points=[],
                keywords=["IE"],
            ),
            usage=None,
        )

        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            new=AsyncMock(return_value=generation),
        ) as mocked_generate:
            result = self._run_async(
                calculate_task_match_once(
                    self.session_factory,
                    self.email_task_id,
                ),
            )

        self.assertIsNotNone(result.run_id)
        used_material = mocked_generate.await_args.kwargs["primary_material"]
        self.assertEqual(used_material.id, current_material_id)
        runs = self._run_async(self._list_runs())
        self.assertEqual(runs[0].primary_material_id, current_material_id)

    def test_calculate_match_injects_intended_research_direction_from_runtime_settings(self) -> None:
        self._run_async(self._set_intended_research_direction("医学自然语言处理"))
        generation = llm_runtime.GeneratedMatchEvaluation(
            result=llm_runtime.MatchEvaluationResult(
                match_score=92,
                match_reason="意向方向与导师方向接近",
                fit_points=["医学 NLP"],
                risk_points=[],
                keywords=["医学 NLP"],
            ),
            usage=None,
        )

        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            new=AsyncMock(return_value=generation),
        ) as mocked_generate:
            self._run_async(calculate_task_match_once(self.session_factory, self.email_task_id))

        self.assertEqual(
            mocked_generate.await_args.kwargs["intended_research_direction"],
            "医学自然语言处理",
        )

    def test_calculate_match_rejects_when_identity_has_no_default_material(self) -> None:
        self._run_async(self._clear_identity_primary_material())

        with (
            patch(
                "app.services.task_runtime.llm_runtime.generate_match_evaluation",
                new=AsyncMock(),
            ) as mocked_generate,
            self.assertRaisesRegex(ValueError, "请到个人页设置默认材料"),
        ):
            self._run_async(calculate_task_match_once(self.session_factory, self.email_task_id))

        mocked_generate.assert_not_awaited()
        runs = self._run_async(self._list_runs())
        self.assertEqual(runs, [])

    def test_calculate_match_persists_failed_token_audit(self) -> None:
        with patch(
            "app.services.task_runtime.llm_runtime.generate_match_evaluation",
            new=AsyncMock(
                side_effect=llm_runtime.LLMRuntimeError(
                    "模型请求失败",
                    endpoint_kind="chat_completions",
                    status_code=500,
                    duration_ms=222,
                ),
            ),
        ):
            result = self._run_async(
                calculate_task_match_once(
                    self.session_factory,
                    self.email_task_id,
                ),
            )

        self.assertIsNone(result.usage.total_tokens)
        self.assertIsNotNone(result.run_id)

        runs = self._run_async(self._list_runs())
        self.assertEqual(len(runs), 1)
        self.assertFalse(runs[0].success)
        self.assertEqual(runs[0].status, "failed")
        self.assertEqual(runs[0].error_kind, "llm_runtime")
        self.assertEqual(runs[0].status_code, 500)
        self.assertIn("模型请求失败", runs[0].error_message)
        self.assertIsNotNone(runs[0].started_at)
        self.assertIsNotNone(runs[0].finished_at)

    def test_calculate_match_rejects_when_another_run_is_running(self) -> None:
        self._run_async(self._insert_running_run())

        with self.assertRaisesRegex(MatchAnalysisAlreadyRunningError, "该任务正在分析中"):
            self._run_async(calculate_task_match_once(self.session_factory, self.email_task_id))

    def test_recover_interrupted_match_analysis_runs_marks_running_run_failed(self) -> None:
        self._run_async(self._insert_running_run())

        recovered = self._run_async(recover_interrupted_match_analysis_runs(self.session_factory))

        self.assertEqual(recovered, 1)
        runs = self._run_async(self._list_runs())
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].status, "failed")
        self.assertFalse(runs[0].success)
        self.assertEqual(runs[0].error_kind, "interrupted")
        self.assertEqual(runs[0].error_message, task_runtime.INTERRUPTED_MATCH_ANALYSIS_RUN_ERROR)
        self.assertIsNotNone(runs[0].finished_at)

    def test_calculate_match_rejects_when_primary_material_has_no_extracted_text(self) -> None:
        self._run_async(self._clear_primary_material_text())

        with (
            patch(
                "app.modules.identities.materials.support.extract_text_from_document",
                return_value=None,
            ),
            patch(
                "app.services.task_runtime.llm_runtime.generate_match_evaluation",
                new=AsyncMock(),
            ) as mocked_generate,
            self.assertRaisesRegex(ValueError, "默认材料无法提取文本"),
        ):
            self._run_async(calculate_task_match_once(self.session_factory, self.email_task_id))

        mocked_generate.assert_not_awaited()
        runs = self._run_async(self._list_runs())
        self.assertEqual(runs, [])

    async def _list_runs(self) -> list[MatchAnalysisRun]:
        async with self.session_factory() as session:
            return list(await session.scalars(select(MatchAnalysisRun)))

    async def _list_canonical_results(self) -> list[IdentityProfessorMatchResult]:
        async with self.session_factory() as session:
            return list(
                await session.scalars(
                    select(IdentityProfessorMatchResult).order_by(
                        IdentityProfessorMatchResult.id.asc(),
                    ),
                ),
            )

    async def _configure_shared_match_source(self) -> tuple[int, int, int]:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, self.email_task_id)
            assert task is not None
            source_identity = await session.get(IdentityProfile, task.identity_id)
            assert source_identity is not None
            assert source_identity.current_primary_material_id is not None

            active_identity = IdentityProfile(
                name="身份 B",
                profile_name="身份 B",
                sender_name="身份 B",
                email_address="sender-b@example.com",
                smtp_host="smtp.example.com",
                smtp_port=465,
                smtp_username="sender-b@example.com",
                smtp_password="secret",
                default_language="zh-CN",
                outreach_generation_mode="llm",
            )
            session.add(active_identity)
            await session.flush()
            active_material = IdentityMaterial(
                identity_id=active_identity.id,
                display_name="身份 B 简历",
                file_path="data/materials/resume-b.txt",
                original_filename="resume-b.txt",
                material_type="resume",
                sha256="c" * 64,
                extracted_text="身份 B 的材料不应参与本次匹配。",
            )
            session.add(active_material)
            await session.flush()
            active_identity.current_primary_material_id = active_material.id

            group = IdentityCommunicationGroup()
            session.add(group)
            await session.flush()
            source_identity.communication_group_id = group.id
            active_identity.communication_group_id = group.id
            group.match_source_identity_id = source_identity.id
            task.identity_id = active_identity.id
            task.primary_material_id = active_material.id
            await session.commit()
            return (
                source_identity.id,
                active_identity.id,
                source_identity.current_primary_material_id,
            )

    async def _load_shared_match_state(self, active_identity_id: int) -> dict[str, object]:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, self.email_task_id)
            assert task is not None
            scope, resolved = await load_resolved_match_result(
                session,
                active_identity_id=active_identity_id,
                professor_id=task.professor_id,
            )
            canonical_results = list(
                await session.scalars(select(IdentityProfessorMatchResult)),
            )
            runs = list(await session.scalars(select(MatchAnalysisRun)))
            return {
                "task": task,
                "canonical_results": canonical_results,
                "runs": runs,
                "resolved_scope_source_id": scope.source_identity_id,
                "resolved_match_reason": resolved.match_reason if resolved else None,
            }

    async def _delete_match_material(self) -> dict[str, object]:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, self.email_task_id)
            assert task is not None
            identity = await session.get(IdentityProfile, task.identity_id)
            assert identity is not None
            assert identity.current_primary_material_id is not None
            deletion = await delete_identity_material_record(
                session,
                identity.current_primary_material_id,
                event_name="identity_material.deleted",
                actor="test",
            )
            await session.commit()
            scope, result = await load_resolved_match_result(
                session,
                active_identity_id=identity.id,
                professor_id=task.professor_id,
                include_legacy_task_snapshots=False,
            )
            assert result is not None
            return {
                "detached_match_result_count": deletion.detached_match_result_count,
                "current_primary_material_id": scope.source_identity.current_primary_material_id,
                "result_primary_material_id": result.primary_material_id,
                "is_stale": match_result_is_stale(result, scope.source_identity),
                "match_score": result.match_score,
            }

    async def _delete_shared_match_source_and_load_state(
        self,
        source_identity_id: int,
        active_identity_id: int,
    ) -> dict[str, object]:
        async with self.session_factory() as session:
            connection = await session.connection()
            await connection.exec_driver_sql("PRAGMA foreign_keys = ON")
            await delete_identity(source_identity_id, session=session)
            task = await session.get(EmailTask, self.email_task_id)
            assert task is not None
            active_identity = await session.get(IdentityProfile, active_identity_id)
            assert active_identity is not None
            _, resolved = await load_resolved_match_result(
                session,
                active_identity_id=active_identity_id,
                professor_id=task.professor_id,
            )
            return {
                "source_identity": await session.get(
                    IdentityProfile,
                    source_identity_id,
                ),
                "active_communication_group_id": active_identity.communication_group_id,
                "canonical_results": list(
                    await session.scalars(select(IdentityProfessorMatchResult)),
                ),
                "runs": list(await session.scalars(select(MatchAnalysisRun))),
                "job_match_source_identity_ids": list(
                    await session.scalars(
                        select(MatchAnalysisJob.match_source_identity_id),
                    ),
                ),
                "job_item_run_ids": list(
                    await session.scalars(
                        select(MatchAnalysisJobItem.match_analysis_run_id),
                    ),
                ),
                "task_match_source_identity_id": task.match_source_identity_id,
                "resolved_match_reason": resolved.match_reason if resolved else None,
            }

    async def _link_latest_run_to_shared_match_job(
        self,
        source_identity_id: int,
        active_identity_id: int,
    ) -> None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, self.email_task_id)
            assert task is not None
            run = await session.scalar(
                select(MatchAnalysisRun).order_by(MatchAnalysisRun.id.desc()),
            )
            assert run is not None
            job = MatchAnalysisJob(
                name="共享依据身份删除测试",
                identity_id=active_identity_id,
                match_source_identity_id=source_identity_id,
                llm_profile_id=task.llm_profile_id,
                status="completed",
                target_count=1,
                succeeded_count=1,
            )
            session.add(job)
            await session.flush()
            session.add(
                MatchAnalysisJobItem(
                    job_id=job.id,
                    professor_id=task.professor_id,
                    email_task_id=task.id,
                    status="succeeded",
                    match_analysis_run_id=run.id,
                ),
            )
            await session.commit()

    async def _clear_shared_source_and_load_match(
        self,
        active_identity_id: int,
    ) -> str | None:
        async with self.session_factory() as session:
            active_identity = await session.get(IdentityProfile, active_identity_id)
            assert active_identity is not None
            assert active_identity.communication_group_id is not None
            group = await session.get(
                IdentityCommunicationGroup,
                active_identity.communication_group_id,
            )
            assert group is not None
            group.match_source_identity_id = None
            await session.commit()
            task = await session.get(EmailTask, self.email_task_id)
            assert task is not None
            _, resolved = await load_resolved_match_result(
                session,
                active_identity_id=active_identity_id,
                professor_id=task.professor_id,
            )
            return resolved.match_reason if resolved is not None else None

    async def _clear_primary_material_text(self) -> None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, self.email_task_id)
            assert task is not None
            material = await session.get(IdentityMaterial, task.primary_material_id)
            assert material is not None
            material.original_filename = "resume.pdf"
            material.file_path = "data/materials/resume.pdf"
            material.extracted_text = None
            await session.commit()

    async def _switch_identity_default_material(self) -> int:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, self.email_task_id)
            assert task is not None
            material = IdentityMaterial(
                identity_id=task.identity_id,
                display_name="新默认材料",
                file_path="data/materials/new-resume.txt",
                original_filename="new-resume.txt",
                material_type="resume",
                sha256="b" * 64,
                extracted_text="我做过智能体规划与工具调用。",
            )
            session.add(material)
            await session.flush()
            identity = await session.get(IdentityProfile, task.identity_id)
            assert identity is not None
            identity.current_primary_material_id = material.id
            await session.commit()
            return material.id

    async def _clear_task_primary_material(self) -> int:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, self.email_task_id)
            assert task is not None
            identity = await session.get(IdentityProfile, task.identity_id)
            assert identity is not None
            assert identity.current_primary_material_id is not None
            current_material_id = identity.current_primary_material_id
            task.primary_material_id = None
            await session.commit()
            return current_material_id

    async def _set_intended_research_direction(self, value: str) -> None:
        async with self.session_factory() as session:
            session.add(AppSetting(id=1, intended_research_direction=value))
            await session.commit()

    async def _clear_identity_primary_material(self) -> None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, self.email_task_id)
            assert task is not None
            identity = await session.get(IdentityProfile, task.identity_id)
            assert identity is not None
            identity.current_primary_material_id = None
            await session.commit()

    async def _insert_running_run(self) -> None:
        async with self.session_factory() as session:
            task = await session.get(EmailTask, self.email_task_id)
            assert task is not None
            session.add(
                MatchAnalysisRun(
                    email_task_id=task.id,
                    professor_id=task.professor_id,
                    identity_id=task.identity_id,
                    llm_profile_id=task.llm_profile_id,
                    status="running",
                    success=False,
                )
            )
            await session.commit()


if __name__ == "__main__":
    unittest.main()
