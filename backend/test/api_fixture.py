from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.migrations import get_alembic_config, get_head_revision
from app.modules.llm.runtime import LLMRuntimeAdaptation
from test.migrated_database import create_migrated_sqlite_database

BACKEND_DIR = Path(__file__).resolve().parents[1]
HEAD_REVISION = get_head_revision(get_alembic_config())


class ApiFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from main import create_app

        cls.client = TestClient(create_app())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "api_test.db"
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{self.db_path.as_posix()}"
        os.environ["ENABLE_BACKGROUND_WORKERS"] = "0"
        create_migrated_sqlite_database(self.db_path)

        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        get_settings.cache_clear()
        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()

        self._task_runtime_adaptation_patch = patch(
            "app.modules.workspace.tasks.runtime.llm_runtime.ensure_llm_runtime_adaptation",
            new=AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None)),
        )
        self._match_task_analysis_adaptation_patch = patch(
            "app.modules.matching.task_analysis.llm_runtime.ensure_llm_runtime_adaptation",
            new=AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None)),
        )
        self._test_compose_runtime_adaptation_patch = patch(
            "app.modules.communications.test_compose.runtime.llm_runtime.ensure_llm_runtime_adaptation",
            new=AsyncMock(return_value=LLMRuntimeAdaptation("chat_completions", None)),
        )
        self._task_runtime_adaptation_patch.start()
        self._match_task_analysis_adaptation_patch.start()
        self._test_compose_runtime_adaptation_patch.start()

    def tearDown(self) -> None:
        self._test_compose_runtime_adaptation_patch.stop()
        self._match_task_analysis_adaptation_patch.stop()
        self._task_runtime_adaptation_patch.stop()
        self.client.cookies.clear()
        from app.core.config import get_settings
        from app.core.database import dispose_engine, get_engine, get_session_factory

        if get_engine.cache_info().currsize:
            asyncio.run(dispose_engine())
        get_session_factory.cache_clear()
        get_settings.cache_clear()
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("ENABLE_BACKGROUND_WORKERS", None)
        self.temp_dir.cleanup()

    def _create_sent_professor_with_later_task(
        self,
        *,
        identity_id: int,
        llm_id: int,
        email: str,
        later_status: str,
        cancellation_reason: str | None = None,
    ) -> int:
        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "已联系后新任务导师",
                "email": email,
                "title": "Professor",
                "university": "Example University",
                "school": "School of AI",
                "department": "Computer Science",
                "research_direction": "Large language models",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]

        ensure_response = self.client.post(
            f"/api/workspaces/{professor_id}/ensure-task",
            params={"identity_id": identity_id, "llm_profile_id": llm_id},
        )
        self.assertEqual(ensure_response.status_code, 200, msg=ensure_response.text)
        parent_task_id = ensure_response.json()["current_task"]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'sent', sent_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (parent_task_id,),
            )
            connection.execute(
                """
                INSERT INTO email_logs (
                    email_task_id, identity_id, llm_profile_id, professor_id,
                    direction, subject, content, rfc_message_id
                )
                VALUES (?, ?, ?, ?, 'sent', 'hello', 'hello body', '<sent@example.edu>')
                """,
                (parent_task_id, identity_id, llm_id, professor_id),
            )
            connection.execute(
                """
                INSERT INTO email_tasks (
                    source, parent_task_id, identity_id, llm_profile_id,
                    professor_id, status, cancellation_reason, created_at, updated_at
                )
                VALUES ('manual', ?, ?, ?, ?, ?, ?, datetime('now', '+1 minute'), datetime('now', '+1 minute'))
                """,
                (
                    parent_task_id,
                    identity_id,
                    llm_id,
                    professor_id,
                    later_status,
                    cancellation_reason,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        return professor_id

    def _create_identity(
        self,
        *,
        with_imap: bool,
        email_address: str = "sender@example.com",
    ) -> int:
        payload = self._build_identity_payload(
            with_imap=with_imap,
            outreach_template_subject="申请与{{name}}老师交流",
            outreach_template_body_text="老师您好，我是{{sender_name}}，关注到您在{{research_direction}}方向的工作。",
        )
        payload["email_address"] = email_address
        response = self.client.post(
            "/api/identities",
            json=payload,
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    @staticmethod
    def _build_identity_payload(
        *,
        with_imap: bool,
        outreach_generation_mode: str = "llm",
        outreach_template_subject: str | None = None,
        outreach_template_body_text: str | None = None,
        outreach_template_body_html: str | None = None,
    ) -> dict[str, object]:
        return {
            "name": "测试身份",
            "email_address": "sender@example.com",
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_username": "different-login@example.com",
            "smtp_password": "secret",
            "imap_host": "imap.example.com" if with_imap else None,
            "imap_port": 993 if with_imap else None,
            "imap_username": "sender@example.com" if with_imap else None,
            "imap_password": "secret" if with_imap else None,
            "default_language": "zh-CN",
            "outreach_generation_mode": outreach_generation_mode,
            "outreach_template_subject": outreach_template_subject,
            "outreach_template_body_text": outreach_template_body_text,
            "outreach_template_body_html": outreach_template_body_html,
            "match_threshold": None,
            "same_domain_cooldown_minutes": None,
            "is_default": True,
        }

    @staticmethod
    def _build_llm_payload(
        *,
        api_base_url: str | None,
    ) -> dict[str, object]:
        return {
            "name": "测试模型",
            "provider": "openai",
            "api_base_url": api_base_url,
            "api_key": "sk-test-key",
            "model_name": "gpt-4o-mini",
            "matcher_prompt_template": None,
            "writer_prompt_template": None,
            "temperature": 0.2,
            "max_tokens": 2048,
            "is_default": True,
        }

    def _create_llm(
        self,
        *,
        name: str = "默认模型",
        model_name: str = "gpt-4o-mini",
    ) -> int:
        response = self.client.post(
            "/api/llm-profiles",
            json={
                "name": name,
                "provider": "openai",
                "api_base_url": "https://api.example.com/v1",
                "api_key": "sk-test-key",
                "model_name": model_name,
                "matcher_prompt_template": "matcher",
                "writer_prompt_template": "writer",
                "temperature": 0.2,
                "max_tokens": 2048,
                "is_default": True,
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    def _create_rewrite_ready_task(self) -> int:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="rewrite-ready-resume.txt",
            content=b"My background is in AI agents and research workflows.",
            material_type="resume",
        )
        professor_id = self._create_professor(
            email=f"rewrite-ready-{datetime.now(UTC).timestamp()}@example.edu"
        )
        return self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="matched",
            primary_material_id=material_id,
            selected_material_ids=[],
            match_score=82,
            match_reason="方向匹配",
            outreach_generation_mode="llm",
        )

    def _create_generating_workspace_rewrite_task(self) -> int:
        task_id = self._create_rewrite_ready_task()
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = ?,
                    draft_generation_previous_status = ?,
                    draft_generation_started_at = ?,
                    draft_rewrite_source_subject = ?,
                    draft_rewrite_source_body_text = ?,
                    draft_rewrite_source_body_html = ?,
                    draft_rewrite_source_selected_material_ids = ?
                WHERE id = ?
                """,
                (
                    "generating_draft",
                    "matched",
                    datetime.now(UTC).isoformat(),
                    "源主题",
                    "源正文",
                    "<p>源正文</p>",
                    json.dumps([]),
                    task_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return task_id

    def _create_scheduled_template_batch(self, *, professor_count: int = 1) -> int:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_ids = [
            self._create_professor(email=f"send-cancellation-{index}@example.edu")
            for index in range(professor_count)
        ]
        scheduled_date = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
        response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "逐项取消定时发送",
                "professor_ids": professor_ids,
                "schedule_type": "scheduled",
                "scheduled_dates": [scheduled_date],
                "window_start_time": "09:00",
                "window_end_time": "18:00",
                "emails_per_window": max(professor_count, 1),
                "primary_material_id": None,
                "email_subject": "申请与{{name}}老师交流",
                "email_body": "老师您好，我是{{sender_name}}。",
                "selected_material_ids": None,
                "outreach_generation_mode": "template",
                "outreach_template_subject": "申请与{{name}}老师交流",
                "outreach_template_body_text": "老师您好，我是{{sender_name}}。",
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()["id"]

    def _create_canceled_batch_stopped_parent_task(self, *, email: str) -> int:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "旧动作拦截导师",
                "email": email,
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Agent systems",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]

        create_response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "旧动作拦截批量任务",
                "professor_ids": [professor_id],
                "schedule_type": "immediate",
                "window_start_time": None,
                "window_end_time": None,
                "emails_per_window": None,
                "primary_material_id": None,
                "email_subject": "联系 {{name}}",
                "email_body": "联系正文 {{name}}",
                "selected_material_ids": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        batch_task_id = create_response.json()["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            task_id = connection.execute(
                """
                SELECT id
                FROM email_tasks
                WHERE batch_task_id = ?
                """,
                (batch_task_id,),
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE batch_tasks
                SET status = ?
                WHERE id = ?
                """,
                ("stopped", batch_task_id),
            )
            connection.execute(
                """
                UPDATE email_tasks
                SET status = ?,
                    cancellation_reason = ?
                WHERE id = ?
                """,
                ("canceled", "batch_stopped", task_id),
            )
            connection.commit()
        finally:
            connection.close()

        return task_id

    def _create_expired_scheduled_batch_review_task(
        self,
        *,
        batch_status: str = "running",
    ) -> tuple[int, int]:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "过期窗口导师",
                "email": f"expired-window-{datetime.now(UTC).timestamp()}@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Agents",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(
            professor_response.status_code, 201, msg=professor_response.text
        )
        professor_id = professor_response.json()["id"]
        scheduled_date = (datetime.now().date() + timedelta(days=1)).isoformat()
        expired_date = (datetime.now().date() - timedelta(days=1)).isoformat()

        create_response = self.client.post(
            "/api/batch-tasks",
            json={
                "identity_id": identity_id,
                "llm_profile_id": llm_id,
                "name": "过期窗口批量任务",
                "professor_ids": [professor_id],
                "schedule_type": "scheduled",
                "scheduled_dates": [scheduled_date],
                "window_start_time": "09:00",
                "window_end_time": "18:00",
                "emails_per_window": 10,
                "primary_material_id": None,
                "email_subject": "申请与{{name}}老师交流",
                "email_body": "老师您好，我是{{sender_name}}。",
                "selected_material_ids": None,
                "outreach_generation_mode": "llm",
                "outreach_template_subject": None,
                "outreach_template_body_text": None,
                "outreach_template_body_html": None,
            },
        )
        self.assertEqual(create_response.status_code, 201, msg=create_response.text)
        batch_task_id = create_response.json()["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            task_id = connection.execute(
                """
                SELECT id
                FROM email_tasks
                WHERE batch_task_id = ?
                """,
                (batch_task_id,),
            ).fetchone()[0]
            connection.execute(
                """
                UPDATE batch_tasks
                SET status = ?,
                    scheduled_dates = ?
                WHERE id = ?
                """,
                (batch_status, json.dumps([expired_date]), batch_task_id),
            )
            connection.execute(
                """
                UPDATE email_tasks
                SET status = ?,
                    generated_subject = ?,
                    generated_content_text = ?,
                    generated_content_html = ?,
                    approved_subject = ?,
                    approved_body_text = ?,
                    approved_body_html = ?,
                    approved_at = ?
                WHERE id = ?
                """,
                (
                    "review_required",
                    "申请与导师交流",
                    "老师您好，我是申请人。",
                    "<p>老师您好，我是申请人。</p>",
                    None,
                    None,
                    None,
                    datetime.now(UTC).isoformat(),
                    task_id,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        return batch_task_id, task_id

    async def _dispatch_due_tasks(self) -> None:
        from app.core.database import get_session_factory
        from app.modules.workspace.tasks.delivery import dispatch_due_tasks_once

        await dispatch_due_tasks_once(get_session_factory(), limit=10)

    async def _poll_replies(self) -> None:
        from app.core.database import get_session_factory
        from app.modules.communications.public import poll_for_replies_once

        await poll_for_replies_once(get_session_factory())

    async def _force_task_due(self, task_id: int) -> None:
        from app.core.database import get_session_factory
        from app.models import EmailTask

        async with get_session_factory()() as session:
            task = await session.get(EmailTask, task_id)
            task.scheduled_at = datetime.now(UTC) - timedelta(minutes=1)
            await session.commit()

    @staticmethod
    def _build_match_evaluation_result(
        *,
        match_score: int,
    ):
        from app.modules.llm.runtime import (
            GeneratedMatchEvaluation,
            MatchEvaluationResult,
        )

        return GeneratedMatchEvaluation(
            result=MatchEvaluationResult(
                match_score=match_score,
                match_reason="研究方向和材料内容高度匹配。",
                fit_points=["研究方向一致", "材料信息完整"],
                risk_points=["尚未展开具体合作切口"],
                keywords=["大模型", "信息提取"],
            ),
            usage=None,
        )

    @staticmethod
    def _build_draft_generation_result(
        *,
        subject: str,
        body_text: str,
        body_html: str,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cached_tokens: int | None = None,
        prompt_hash: str | None = None,
        stable_prefix_hash: str | None = None,
        prompt_cache_key: str | None = None,
    ):
        from app.modules.llm.runtime import (
            ChatCompletionUsage,
            DraftGenerationResult,
            GeneratedDraftContent,
        )

        return GeneratedDraftContent(
            result=DraftGenerationResult(
                subject=subject,
                body_text=body_text,
                body_html=body_html,
            ),
            usage=(
                ChatCompletionUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=(
                        (prompt_tokens or 0) + (completion_tokens or 0)
                        if prompt_tokens is not None and completion_tokens is not None
                        else None
                    ),
                    cached_tokens=cached_tokens,
                )
                if prompt_tokens is not None or completion_tokens is not None
                else None
            ),
            prompt_hash=prompt_hash,
            stable_prefix_hash=stable_prefix_hash,
            prompt_cache_key=prompt_cache_key,
        )

    def _create_professor(self, *, email: str = "professor@example.edu") -> int:
        response = self.client.post(
            "/api/professors",
            json={
                "name": "材料删除测试导师",
                "email": email,
                "title": "Professor",
                "university": "Example University",
                "school": "School of Computing",
                "department": "Computer Science",
                "research_direction": "Agents",
                "recent_papers": [],
                "profile_url": None,
                "source_url": None,
            },
        )
        self.assertEqual(response.status_code, 201, msg=response.text)
        return response.json()["id"]

    def _insert_email_task_with_material(
        self,
        *,
        identity_id: int,
        llm_id: int,
        professor_id: int,
        status: str,
        primary_material_id: int | None,
        selected_material_ids: list[int] | None = None,
        batch_task_id: int | None = None,
        source: str = "manual",
        generated_subject: str | None = None,
        generated_content_text: str | None = None,
        generated_content_html: str | None = None,
        approved_subject: str | None = None,
        approved_body_text: str | None = None,
        approved_body_html: str | None = None,
        match_score: int | None = None,
        match_reason: str | None = None,
        outreach_generation_mode: str | None = None,
    ) -> int:
        connection = sqlite3.connect(self.db_path)
        try:
            approved_at = (
                "datetime('now')"
                if approved_subject is not None
                or approved_body_text is not None
                or approved_body_html is not None
                else "NULL"
            )
            task_id = connection.execute(
                f"""
                INSERT INTO email_tasks (
                    source, batch_task_id, identity_id, llm_profile_id, professor_id,
                    status, primary_material_id, selected_material_ids, last_error,
                    generated_subject, generated_content_text, generated_content_html,
                    outreach_generation_mode,
                    approved_subject, approved_body_text, approved_body_html,
                    approved_at, match_score, match_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {approved_at}, ?, ?)
                RETURNING id
                """,
                (
                    source,
                    batch_task_id,
                    identity_id,
                    llm_id,
                    professor_id,
                    status,
                    primary_material_id,
                    json.dumps(selected_material_ids)
                    if selected_material_ids is not None
                    else None,
                    "失败任务错误"
                    if status in {"draft_failed", "send_failed"}
                    else None,
                    generated_subject,
                    generated_content_text,
                    generated_content_html,
                    outreach_generation_mode,
                    approved_subject,
                    approved_body_text,
                    approved_body_html,
                    match_score,
                    match_reason,
                ),
            ).fetchone()[0]
            connection.commit()
        finally:
            connection.close()
        return task_id

    def _get_batch_task_status(self, batch_task_id: int) -> str:
        connection = sqlite3.connect(self.db_path)
        try:
            return connection.execute(
                "SELECT status FROM batch_tasks WHERE id = ?",
                (batch_task_id,),
            ).fetchone()[0]
        finally:
            connection.close()

    def _get_task_material_references(
        self, task_id: int
    ) -> tuple[int | None, list[int] | None]:
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT primary_material_id, selected_material_ids FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        finally:
            connection.close()
        selected_material_ids = json.loads(row[1]) if row[1] is not None else None
        return row[0], selected_material_ids

    def _mark_email_task_sent(self, task_id: int, *, minutes_ago: int) -> None:
        modifier = f"-{minutes_ago} minutes"
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET status = 'sent',
                    sent_at = datetime('now', ?),
                    updated_at = datetime('now', ?)
                WHERE id = ?
                """,
                (modifier, modifier, task_id),
            )
            connection.commit()
        finally:
            connection.close()

    def _insert_match_analysis_run(
        self,
        *,
        task_id: int,
        identity_id: int,
        llm_id: int,
        professor_id: int,
        primary_material_id: int | None,
    ) -> int:
        connection = sqlite3.connect(self.db_path)
        try:
            run_id = connection.execute(
                """
                INSERT INTO match_analysis_runs (
                    email_task_id, professor_id, identity_id, llm_profile_id,
                    primary_material_id, status, success, match_score
                )
                VALUES (?, ?, ?, ?, ?, 'succeeded', 1, 82)
                RETURNING id
                """,
                (task_id, professor_id, identity_id, llm_id, primary_material_id),
            ).fetchone()[0]
            connection.commit()
        finally:
            connection.close()
        return run_id

    def _get_match_analysis_run_primary_material_id(self, run_id: int) -> int | None:
        connection = sqlite3.connect(self.db_path)
        try:
            return connection.execute(
                "SELECT primary_material_id FROM match_analysis_runs WHERE id = ?",
                (run_id,),
            ).fetchone()[0]
        finally:
            connection.close()

    def _get_email_task_llm_profile_id(self, task_id: int) -> int:
        connection = sqlite3.connect(self.db_path)
        try:
            return connection.execute(
                "SELECT llm_profile_id FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()[0]
        finally:
            connection.close()

    def _get_email_task_delete_state(self, task_id: int) -> dict[str, object | None]:
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT status, primary_material_id, selected_material_ids,
                       generated_subject, generated_content_text, generated_content_html,
                       approved_subject, approved_body_text, approved_body_html,
                       approved_at, last_error, cancellation_reason
                FROM email_tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
        finally:
            connection.close()
        return {
            "status": row[0],
            "primary_material_id": row[1],
            "selected_material_ids": json.loads(row[2]) if row[2] is not None else None,
            "generated_subject": row[3],
            "generated_content_text": row[4],
            "generated_content_html": row[5],
            "approved_subject": row[6],
            "approved_body_text": row[7],
            "approved_body_html": row[8],
            "approved_at": row[9],
            "last_error": row[10],
            "cancellation_reason": row[11],
        }

    def _insert_batch_task_with_material(
        self,
        *,
        identity_id: int,
        llm_id: int,
        status: str,
        primary_material_id: int | None,
        selected_material_ids: list[int] | None = None,
        deleted: bool = False,
    ) -> int:
        connection = sqlite3.connect(self.db_path)
        try:
            batch_task_id = connection.execute(
                """
                INSERT INTO batch_tasks (
                    identity_id, llm_profile_id, name, status,
                    primary_material_id, selected_material_ids, target_count, deleted_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END)
                RETURNING id
                """,
                (
                    identity_id,
                    llm_id,
                    "材料删除测试批量任务",
                    status,
                    primary_material_id,
                    json.dumps(selected_material_ids)
                    if selected_material_ids is not None
                    else None,
                    1,
                    1 if deleted else 0,
                ),
            ).fetchone()[0]
            connection.commit()
        finally:
            connection.close()
        return batch_task_id

    def _get_batch_task_material_references(
        self, batch_task_id: int
    ) -> tuple[int | None, list[int] | None]:
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT primary_material_id, selected_material_ids FROM batch_tasks WHERE id = ?",
                (batch_task_id,),
            ).fetchone()
        finally:
            connection.close()
        selected_material_ids = json.loads(row[1]) if row[1] is not None else None
        return row[0], selected_material_ids

    def _upload_material(
        self,
        identity_id: int,
        *,
        filename: str,
        content: bytes,
        material_type: str,
    ) -> int:
        response = self.client.post(
            f"/api/identities/{identity_id}/materials",
            files={"file": (filename, io.BytesIO(content), "application/octet-stream")},
            data={"material_type": material_type},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    @staticmethod
    def _build_probe_result(
        *, ok: bool, message: str, resolved_base_url: str, response_preview: str
    ):
        from app.modules.llm.runtime import LLMProbeResult

        return LLMProbeResult(
            ok=ok,
            message=message,
            resolved_base_url=resolved_base_url,
            response_preview=response_preview,
        )

    @staticmethod
    def _build_model_catalog_result(
        *,
        ok: bool,
        message: str,
        resolved_base_url: str,
        models: list[str],
        selected_model_available: bool,
    ):
        from app.modules.llm.runtime import LLMModelCatalogResult

        return LLMModelCatalogResult(
            ok=ok,
            message=message,
            resolved_base_url=resolved_base_url,
            models=models,
            selected_model_available=selected_model_available,
        )

    def _latest_email_log_provider_payload(self) -> dict[str, object]:
        connection = sqlite3.connect(self.db_path)
        try:
            raw_payload = connection.execute(
                """
                SELECT provider_payload
                FROM email_logs
                WHERE direction = 'draft'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()[0]
        finally:
            connection.close()
        if isinstance(raw_payload, str):
            parsed = json.loads(raw_payload)
            if isinstance(parsed, dict):
                return parsed
        self.fail("未找到草稿 provider_payload")

    @staticmethod
    def _build_send_result(*, message_id: str, provider_payload: dict[str, str]):
        from app.modules.communications.transport import SendMailResult

        return SendMailResult(message_id=message_id, provider_payload=provider_payload)

    @staticmethod
    def _build_received_email(
        *,
        from_email: str,
        subject: str,
        content: str,
        message_id: str,
        in_reply_to: str,
        sent_at: datetime | None = None,
        received_at: datetime | None = None,
    ):
        from app.modules.communications.transport import ReceivedEmail

        return ReceivedEmail(
            from_email=from_email,
            subject=subject,
            content=content,
            content_html=None,
            message_id=message_id,
            in_reply_to=in_reply_to,
            references=in_reply_to,
            sent_at=sent_at or datetime.now(UTC),
            headers={
                "from": from_email,
                "subject": subject,
                "message_id": message_id,
                "in_reply_to": in_reply_to,
                "references": in_reply_to,
                "to": "sender@example.com",
            },
            received_at=received_at,
        )

    @staticmethod
    def _build_imap_fetched_message(
        *,
        from_email: str,
        subject: str,
        content: str,
        message_id: str,
        in_reply_to: str,
        sent_at: datetime | None = None,
        received_at: datetime | None = None,
    ):
        from app.modules.communications.imap.fetcher import ImapFetchedMessage

        return ImapFetchedMessage(
            uid=1,
            from_email=from_email,
            subject=subject,
            message_id=message_id,
            in_reply_to=in_reply_to,
            references=in_reply_to,
            sent_at=sent_at or datetime.now(UTC),
            received_at=received_at,
            headers={
                "from": from_email,
                "subject": subject,
                "message_id": message_id,
                "in_reply_to": in_reply_to,
                "references": in_reply_to,
                "to": "sender@example.com",
            },
            body_text=content,
            body_html=None,
        )

    @staticmethod
    def _run_async(coro):
        return asyncio.run(coro)
