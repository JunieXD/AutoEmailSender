from __future__ import annotations

import io
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.core.migrations import get_alembic_config, get_head_revision
from app.modules.llm.runtime import LLMRuntimeAdaptation

BACKEND_DIR = Path(__file__).resolve().parents[1]
HEAD_REVISION = get_head_revision(get_alembic_config())


from test.api_fixture import ApiFixture


class ProfilesCommunicationsApiTests(ApiFixture):
    def test_email_delivery_pagination_accepts_one_item_per_page(self) -> None:
        response = self.client.get("/api/email-deliveries?page_size=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["page_size"], 1)

    def test_email_delivery_mutations_use_lossless_concurrency_token(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="delivery-concurrency@example.edu")
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="scheduled",
            primary_material_id=None,
            approved_subject="并发令牌测试",
            approved_body_text="测试正文",
        )
        exact_updated_at = "2026-09-03 11:47:51.386236"
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE email_tasks
                SET scheduled_at = ?, updated_at = ?
                WHERE id = ?
                """,
                ("2099-09-04 12:50:00.000000", exact_updated_at, task_id),
            )
            connection.commit()
        finally:
            connection.close()

        listed = self.client.get(
            "/api/email-deliveries",
            params={"view": "upcoming", "task_id": task_id},
        )

        self.assertEqual(listed.status_code, 200, msg=listed.text)
        item = listed.json()["items"][0]
        self.assertEqual(item["updated_at"], "2026-09-03T11:47:51Z")
        self.assertEqual(
            item["expected_updated_at"],
            "2026-09-03T11:47:51.386236+00:00",
        )

        stale = self.client.patch(
            f"/api/email-deliveries/{task_id}/schedule",
            json={
                "scheduled_at": "2099-09-05T12:50:00Z",
                "expected_updated_at": item["updated_at"],
            },
        )

        self.assertEqual(stale.status_code, 409, msg=stale.text)

        rescheduled = self.client.patch(
            f"/api/email-deliveries/{task_id}/schedule",
            json={
                "scheduled_at": "2099-09-05T12:50:00Z",
                "expected_updated_at": item["expected_updated_at"],
            },
        )

        self.assertEqual(rescheduled.status_code, 200, msg=rescheduled.text)
        refreshed = self.client.get(
            "/api/email-deliveries",
            params={"view": "upcoming", "task_id": task_id},
        ).json()["items"][0]
        self.assertNotEqual(
            refreshed["expected_updated_at"], item["expected_updated_at"]
        )

        canceled = self.client.post(
            f"/api/email-deliveries/{task_id}/cancel",
            json={"expected_updated_at": refreshed["expected_updated_at"]},
        )

        self.assertEqual(canceled.status_code, 200, msg=canceled.text)

    def test_identity_and_llm_connectivity_endpoints(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        identities = self.client.get("/api/identities").json()
        created_identity = next(
            item for item in identities if item["id"] == identity_id
        )

        with (
            patch(
                "app.modules.identities.profiles.api.test_smtp_connection",
                AsyncMock(return_value=(True, "SMTP 连接测试成功")),
            ),
            patch(
                "app.modules.identities.profiles.api.test_imap_connection",
                AsyncMock(return_value=(True, "IMAP 连接测试成功")),
            ),
            patch(
                "app.modules.llm.api.ensure_llm_runtime_adaptation",
                AsyncMock(
                    return_value=LLMRuntimeAdaptation(
                        "chat_completions", {"enable_thinking": False}
                    )
                ),
            ),
            patch(
                "app.modules.llm.api.probe_llm_profile",
                AsyncMock(
                    return_value=self._build_probe_result(
                        ok=True,
                        message="模型连通性测试成功",
                        resolved_base_url="https://api.example.com/v1",
                        response_preview="READY",
                    ),
                ),
            ),
        ):
            smtp_result = self.client.post(f"/api/identities/{identity_id}/smtp-test")
            imap_result = self.client.post(f"/api/identities/{identity_id}/imap-test")
            llm_result = self.client.post(f"/api/llm-profiles/{llm_id}/test")

        self.assertEqual(smtp_result.status_code, 200)
        self.assertTrue(smtp_result.json()["ok"])
        self.assertIsNone(smtp_result.json()["possible_cause"])
        self.assertEqual(imap_result.status_code, 200)
        self.assertTrue(imap_result.json()["ok"])
        self.assertEqual(llm_result.status_code, 200)
        self.assertTrue(llm_result.json()["ok"])
        self.assertEqual(created_identity["smtp_username"], "sender@example.com")
        self.assertEqual(created_identity["imap_host"], "imap.example.com")
        self.assertEqual(created_identity["imap_port"], 993)
        self.assertEqual(created_identity["imap_username"], "sender@example.com")
        self.assertEqual(created_identity["imap_password"], "secret")

    def test_smtp_connectivity_failure_includes_possible_cause_and_raw_error(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        raw_error = (
            "SMTP 登录凭据编码失败：UnicodeEncodeError("
            "error_code=SMTP_PASSWORD_NON_ASCII, field=smtp_password, "
            "encoding=ascii, start=6, end=7, reason=ordinal not in range(128))"
        )

        with patch(
            "app.modules.identities.profiles.api.test_smtp_connection",
            AsyncMock(return_value=(False, raw_error)),
        ):
            response = self.client.post(f"/api/identities/{identity_id}/smtp-test")

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["message"], raw_error)
        self.assertIn("邮箱授权码", payload["possible_cause"])
        self.assertIn("不可见字符", payload["possible_cause"])

    def test_identity_accepts_profile_name_and_sender_name_with_name_compatibility(
        self,
    ) -> None:
        payload = self._build_identity_payload(
            with_imap=False,
            outreach_template_subject="申请与{{name}}老师交流",
            outreach_template_body_text="老师您好，我是{{sender_name}}。",
        )
        payload["name"] = "兼容配置名称"
        payload["profile_name"] = "博士申请配置"
        payload["sender_name"] = "王同学"
        payload["email_address"] = "sender-profile-name@example.com"
        payload["smtp_username"] = "sender-profile-name@example.com"

        response = self.client.post("/api/identities", json=payload)

        self.assertEqual(response.status_code, 201, msg=response.text)
        body = response.json()
        self.assertEqual(body["name"], "博士申请配置")
        self.assertEqual(body["profile_name"], "博士申请配置")
        self.assertEqual(body["sender_name"], "王同学")

        list_payload = self.client.get("/api/identities").json()
        created = next(item for item in list_payload if item["id"] == body["id"])
        self.assertEqual(created["name"], "博士申请配置")
        self.assertEqual(created["profile_name"], "博士申请配置")
        self.assertEqual(created["sender_name"], "王同学")

    def test_identity_legacy_name_populates_profile_and_sender_name(self) -> None:
        payload = self._build_identity_payload(
            with_imap=False,
            outreach_template_subject="申请与{{name}}老师交流",
            outreach_template_body_text="老师您好，我是{{sender_name}}。",
        )
        payload["email_address"] = "legacy-name@example.com"
        payload["smtp_username"] = "legacy-name@example.com"
        payload.pop("profile_name", None)
        payload.pop("sender_name", None)
        payload["name"] = "旧身份名称"

        response = self.client.post("/api/identities", json=payload)

        self.assertEqual(response.status_code, 201, msg=response.text)
        body = response.json()
        self.assertEqual(body["name"], "旧身份名称")
        self.assertEqual(body["profile_name"], "旧身份名称")
        self.assertEqual(body["sender_name"], "旧身份名称")

    def test_identity_update_ignores_internal_send_limit_fields(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                UPDATE identity_profiles
                SET daily_send_limit = ?, send_interval_min = ?, send_interval_max = ?
                WHERE id = ?
                """,
                (10, 2, 6, identity_id),
            )
            connection.commit()
        finally:
            connection.close()

        payload = self._build_identity_payload(
            with_imap=False,
            outreach_template_subject="申请与{{name}}老师交流",
            outreach_template_body_text="老师您好，我是{{sender_name}}。",
        )
        payload["daily_send_limit"] = 999
        payload["send_interval_min"] = 99
        payload["send_interval_max"] = 100

        response = self.client.put(f"/api/identities/{identity_id}", json=payload)

        self.assertEqual(response.status_code, 200, msg=response.text)
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                """
                SELECT daily_send_limit, send_interval_min, send_interval_max
                FROM identity_profiles
                WHERE id = ?
                """,
                (identity_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, (10, 2, 6))

    def test_identity_create_rejects_duplicate_email_address(self) -> None:
        payload = self._build_identity_payload(
            with_imap=False,
            outreach_template_subject="申请与{{name}}老师交流",
            outreach_template_body_text="老师您好，我是{{sender_name}}。",
        )
        first_response = self.client.post("/api/identities", json=payload)
        self.assertEqual(first_response.status_code, 201, msg=first_response.text)

        duplicate_response = self.client.post("/api/identities", json=payload)

        self.assertEqual(
            duplicate_response.status_code, 409, msg=duplicate_response.text
        )
        self.assertEqual(
            duplicate_response.json()["detail"],
            "该发件邮箱已存在，请改用编辑已有身份或更换邮箱",
        )

    def test_identity_update_rejects_duplicate_email_address(self) -> None:
        first_payload = self._build_identity_payload(
            with_imap=False,
            outreach_template_subject="申请与{{name}}老师交流",
            outreach_template_body_text="老师您好，我是{{sender_name}}。",
        )
        first_payload["email_address"] = "first-identity@example.com"
        first_payload["smtp_username"] = "first-identity@example.com"
        first_response = self.client.post("/api/identities", json=first_payload)
        self.assertEqual(first_response.status_code, 201, msg=first_response.text)

        second_payload = self._build_identity_payload(
            with_imap=False,
            outreach_template_subject="申请与{{name}}老师交流",
            outreach_template_body_text="老师您好，我是{{sender_name}}。",
        )
        second_payload["email_address"] = "second-identity@example.com"
        second_payload["smtp_username"] = "second-identity@example.com"
        second_response = self.client.post("/api/identities", json=second_payload)
        self.assertEqual(second_response.status_code, 201, msg=second_response.text)

        update_payload = dict(second_payload)
        update_payload["email_address"] = "first-identity@example.com"
        update_payload["smtp_username"] = "first-identity@example.com"
        conflict_response = self.client.put(
            f"/api/identities/{second_response.json()['id']}",
            json=update_payload,
        )

        self.assertEqual(conflict_response.status_code, 409, msg=conflict_response.text)
        self.assertEqual(
            conflict_response.json()["detail"],
            "该发件邮箱已存在，请改用编辑已有身份或更换邮箱",
        )

    def test_llm_model_catalog_endpoint(self) -> None:
        llm_id = self._create_llm()

        with patch(
            "app.modules.llm.api.fetch_llm_profile_models",
            AsyncMock(
                return_value=self._build_model_catalog_result(
                    ok=True,
                    message="已获取 2 个模型",
                    resolved_base_url="https://api.example.com/v1",
                    models=["gpt-5.4", "gpt-5.4-mini"],
                    selected_model_available=True,
                ),
            ),
        ):
            response = self.client.get(f"/api/llm-profiles/{llm_id}/models")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["models"], ["gpt-5.4", "gpt-5.4-mini"])
        self.assertTrue(body["selected_model_available"])

    def test_llm_model_preview_endpoints_use_unsaved_payload(self) -> None:
        payload = self._build_llm_payload(api_base_url="https://draft.example.com/v1")

        with (
            patch(
                "app.modules.llm.api.fetch_llm_profile_models",
                AsyncMock(
                    return_value=self._build_model_catalog_result(
                        ok=True,
                        message="已获取 2 个模型",
                        resolved_base_url="https://draft.example.com/v1",
                        models=["gpt-4o", "gpt-4o-mini"],
                        selected_model_available=True,
                    ),
                ),
            ) as fetch_mock,
            patch(
                "app.modules.llm.api.probe_llm_profile",
                AsyncMock(
                    return_value=self._build_probe_result(
                        ok=True,
                        message="模型连通性测试成功",
                        resolved_base_url="https://draft.example.com/v1",
                        response_preview="READY",
                    ),
                ),
            ) as probe_mock,
            patch(
                "app.modules.llm.api.ensure_llm_runtime_adaptation",
                AsyncMock(
                    return_value=LLMRuntimeAdaptation(
                        "chat_completions", {"enable_thinking": False}
                    )
                ),
            ) as adaptation_mock,
        ):
            models_response = self.client.post(
                "/api/llm-profiles/preview/models", json=payload
            )
            test_response = self.client.post(
                "/api/llm-profiles/preview/test", json=payload
            )

        self.assertEqual(models_response.status_code, 200, msg=models_response.text)
        self.assertEqual(test_response.status_code, 200, msg=test_response.text)
        fetch_mock.assert_awaited_once()
        probe_mock.assert_awaited_once()
        adaptation_mock.assert_awaited_once()
        self.assertEqual(
            fetch_mock.await_args.args[0].api_base_url, payload["api_base_url"]
        )
        self.assertEqual(fetch_mock.await_args.args[0].api_key, payload["api_key"])
        self.assertEqual(
            probe_mock.await_args.args[0].api_base_url, payload["api_base_url"]
        )
        self.assertEqual(
            probe_mock.await_args.args[0].model_name, payload["model_name"]
        )

    def test_llm_profile_preview_test_commits_thinking_adaptation_cache(self) -> None:
        payload = self._build_llm_payload(
            api_base_url="https://cache-preview.example.com/v1"
        )
        payload["model_name"] = "cache-preview-model"

        async def record_adaptation(session, profile):
            from app.modules.llm.adaptation.thinking import record_thinking_adaptation

            await record_thinking_adaptation(
                session,
                api_base_url=profile.api_base_url,
                model_name=profile.model_name,
                learned_extra_body={"enable_thinking": False},
            )
            return LLMRuntimeAdaptation("chat_completions", {"enable_thinking": False})

        with (
            patch(
                "app.modules.llm.api.ensure_llm_runtime_adaptation",
                side_effect=record_adaptation,
            ),
            patch(
                "app.modules.llm.api.probe_llm_profile",
                AsyncMock(
                    return_value=self._build_probe_result(
                        ok=True,
                        message="模型可用性测试成功",
                        resolved_base_url="https://cache-preview.example.com/v1",
                        response_preview="READY",
                    )
                ),
            ),
        ):
            response = self.client.post("/api/llm-profiles/preview/test", json=payload)

        self.assertEqual(response.status_code, 200, msg=response.text)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            row = connection.execute(
                """
                SELECT learned_extra_body
                FROM thinking_adaptation_cache
                WHERE api_base_url = ? AND model_name = ?
                """,
                ("https://cache-preview.example.com/v1", "cache-preview-model"),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(json.loads(row[0]), {"enable_thinking": False})

    def test_llm_profile_test_detects_responses_protocol_and_commits_both_adaptation_caches(
        self,
    ) -> None:
        payload = self._build_llm_payload(
            api_base_url="https://responses-only.example.com/v1"
        )
        payload["model_name"] = "responses-only-model"
        calls: list[str] = []

        class FakeResponse:
            status_code = 200

            def __init__(self, body: dict[str, object]) -> None:
                self._body = body
                self.text = json.dumps(body)

            def json(self) -> dict[str, object]:
                return self._body

        class FakeAsyncClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def post(self, url: str, **kwargs: object) -> FakeResponse:
                calls.append(url)
                return FakeResponse({"output_text": "OK"})

        with patch("app.modules.llm.runtime.httpx.AsyncClient", FakeAsyncClient):
            preview_response = self.client.post(
                "/api/llm-profiles/preview/test", json=payload
            )
            created_response = self.client.post("/api/llm-profiles", json=payload)
            profile_id = created_response.json()["id"]
            saved_response = self.client.post(f"/api/llm-profiles/{profile_id}/test")

        self.assertEqual(preview_response.status_code, 200, msg=preview_response.text)
        self.assertEqual(saved_response.status_code, 200, msg=saved_response.text)
        for response in (preview_response, saved_response):
            data = response.json()
            self.assertTrue(data["ok"])
            self.assertEqual(data["endpoint_kind"], "responses")
            self.assertEqual(
                data["request_url"], "https://responses-only.example.com/v1/responses"
            )
        self.assertEqual(
            preview_response.json()["attempted_urls"],
            [
                "https://responses-only.example.com/v1/chat/completions",
                "https://responses-only.example.com/v1/responses",
            ],
        )
        self.assertEqual(
            calls[0], "https://responses-only.example.com/v1/chat/completions"
        )
        self.assertEqual(calls[1], "https://responses-only.example.com/v1/responses")
        self.assertTrue(all(url.endswith("/responses") for url in calls[1:]))

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            endpoint_row = connection.execute(
                """
                SELECT learned_endpoint_kind
                FROM llm_endpoint_adaptation_cache
                WHERE api_base_url = ? AND model_name = ?
                """,
                ("https://responses-only.example.com/v1", "responses-only-model"),
            ).fetchone()
            thinking_row = connection.execute(
                """
                SELECT learned_extra_body
                FROM thinking_adaptation_cache
                WHERE api_base_url = ? AND model_name = ? AND endpoint_kind = ?
                """,
                (
                    "https://responses-only.example.com/v1",
                    "responses-only-model",
                    "responses",
                ),
            ).fetchone()
        self.assertEqual(endpoint_row, ("responses",))
        self.assertIsNotNone(thinking_row)
        self.assertIsNone(json.loads(thinking_row[0]))

    def test_llm_profile_preview_test_returns_failure_when_thinking_adaptation_fails(
        self,
    ) -> None:
        from app.modules.llm.runtime import LLMRuntimeError

        payload = self._build_llm_payload(api_base_url="https://tls.example.com/v1")
        error = LLMRuntimeError(
            "模型服务 TLS 连接失败，请检查系统代理、网络或稍后重试。",
            request_url="https://tls.example.com/v1/chat/completions",
            attempted_urls=["https://tls.example.com/v1/chat/completions"],
            endpoint_kind="chat_completions",
            duration_ms=23,
        )

        with (
            patch(
                "app.modules.llm.api.ensure_llm_runtime_adaptation",
                AsyncMock(side_effect=error),
            ),
            patch("app.modules.llm.api.probe_llm_profile", AsyncMock()) as probe_mock,
        ):
            response = self.client.post("/api/llm-profiles/preview/test", json=payload)

        self.assertEqual(response.status_code, 200, msg=response.text)
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertEqual(
            data["message"], "模型服务 TLS 连接失败，请检查系统代理、网络或稍后重试。"
        )
        self.assertEqual(
            data["request_url"], "https://tls.example.com/v1/chat/completions"
        )
        self.assertEqual(
            data["attempted_urls"], ["https://tls.example.com/v1/chat/completions"]
        )
        self.assertEqual(data["endpoint_kind"], "chat_completions")
        self.assertEqual(data["duration_ms"], 23)
        self.assertNotIn("_ssl.c", response.text)
        probe_mock.assert_not_awaited()

    def test_identity_template_import_endpoint_supports_unsaved_identity_flow(
        self,
    ) -> None:
        response = self.client.post(
            "/api/identities/template-import",
            files={
                "file": (
                    "template.html",
                    io.BytesIO(
                        "<p>{{name}}老师您好，</p><p>我是{{sender_name}}。</p>".encode(
                            "utf-8"
                        )
                    ),
                    "text/html",
                )
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        payload = response.json()
        self.assertIsNone(payload["subject"])
        self.assertEqual(payload["format_name"], "html")
        self.assertEqual(
            payload["body_text"], "{{name}}老师您好，\n\n我是{{sender_name}}。"
        )
        self.assertIn("<p>{{name}}老师您好，</p>", payload["body_html"])

    def test_identity_template_import_endpoint_requires_file_name(self) -> None:
        boundary = "X-BOUNDARY"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename=""\r\n'
            "Content-Type: text/plain\r\n\r\n"
            "老师您好\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        response = self.client.post(
            "/api/identities/template-import",
            content=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

        self.assertEqual(response.status_code, 400, msg=response.text)
        self.assertEqual(response.json()["detail"], "请选择模板文件")

    def test_identity_allows_missing_template_subject_in_all_modes(self) -> None:
        for mode in ("llm", "template"):
            with self.subTest(mode=mode):
                payload = self._build_identity_payload(
                    with_imap=False,
                    outreach_generation_mode=mode,
                    outreach_template_subject=None,
                    outreach_template_body_text="老师您好，我是{{sender_name}}。",
                    outreach_template_body_html="<p>老师您好，我是{{sender_name}}。</p>",
                )
                payload["email_address"] = f"sender-{mode}@example.com"
                payload["smtp_username"] = f"sender-{mode}@example.com"
                response = self.client.post(
                    "/api/identities",
                    json=payload,
                )

                self.assertEqual(response.status_code, 201, msg=response.text)
                self.assertIsNone(response.json()["outreach_template_subject"])

    def test_identity_allows_missing_plain_text_template_body_even_when_html_exists(
        self,
    ) -> None:
        response = self.client.post(
            "/api/identities",
            json=self._build_identity_payload(
                with_imap=False,
                outreach_generation_mode="llm",
                outreach_template_subject="申请与{{name}}老师交流",
                outreach_template_body_text=None,
                outreach_template_body_html="<p>老师您好，我是{{sender_name}}。</p>",
            ),
        )

        self.assertEqual(response.status_code, 201, msg=response.text)
        self.assertIsNone(response.json()["outreach_template_body_text"])
        self.assertEqual(
            response.json()["outreach_template_body_html"],
            "<p>老师您好，我是{{sender_name}}。</p>",
        )

    def test_llm_structured_result_validation_rejects_invalid_json(self) -> None:
        from app.modules.llm.runtime import (
            DraftGenerationResult,
            LLMRuntimeError,
            parse_structured_result,
        )

        with self.assertRaises(LLMRuntimeError):
            parse_structured_result('{"subject":"only-subject"}', DraftGenerationResult)

    def test_identity_default_template_keeps_legacy_fields_in_sync(self) -> None:
        identity_payload = self._build_identity_payload(
            with_imap=False,
            outreach_template_subject=None,
            outreach_template_body_text=None,
        )
        identity_response = self.client.post("/api/identities", json=identity_payload)
        self.assertEqual(identity_response.status_code, 201, msg=identity_response.text)
        identity_id = identity_response.json()["id"]

        template_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "博士申请中文模板",
                "recommended_generation_mode": "template",
                "subject": "申请与{{name}}老师交流",
                "body_text": "{{name}}老师您好，我是{{sender_name}}。",
                "body_html": "<p>{{name}}老师您好，我是{{sender_name}}。</p>",
            },
        )
        self.assertEqual(template_response.status_code, 201, msg=template_response.text)
        template_id = template_response.json()["id"]

        default_response = self.client.put(
            f"/api/identities/{identity_id}/default-template",
            json={"template_id": template_id},
        )
        self.assertEqual(default_response.status_code, 200, msg=default_response.text)
        default_identity = default_response.json()
        self.assertEqual(default_identity["default_outreach_template_id"], template_id)
        self.assertEqual(default_identity["outreach_generation_mode"], "template")
        self.assertEqual(
            default_identity["outreach_template_body_text"],
            "{{name}}老师您好，我是{{sender_name}}。",
        )

        updated_template = self.client.put(
            f"/api/outreach-templates/{template_id}",
            json={
                "body_text": "更新后的模板正文",
                "body_html": "<p>更新后的模板正文</p>",
            },
        )
        self.assertEqual(updated_template.status_code, 200, msg=updated_template.text)
        refreshed_identity = next(
            item
            for item in self.client.get("/api/identities").json()
            if item["id"] == identity_id
        )
        self.assertEqual(
            refreshed_identity["outreach_template_body_text"], "更新后的模板正文"
        )

        archived = self.client.delete(f"/api/outreach-templates/{template_id}")
        self.assertEqual(archived.status_code, 200, msg=archived.text)
        refreshed_identity = next(
            item
            for item in self.client.get("/api/identities").json()
            if item["id"] == identity_id
        )
        self.assertIsNone(refreshed_identity["default_outreach_template_id"])
        self.assertIsNone(refreshed_identity["outreach_template_body_text"])

    def test_deleting_identity_does_not_delete_its_independent_template(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        identity = next(
            item
            for item in self.client.get("/api/identities").json()
            if item["id"] == identity_id
        )
        template_id = identity["default_outreach_template_id"]
        self.assertIsNotNone(template_id)

        impact_response = self.client.get(
            f"/api/identities/{identity_id}/deletion-impact"
        )
        self.assertEqual(impact_response.status_code, 200, msg=impact_response.text)
        self.assertTrue(impact_response.json()["can_delete"])
        delete_response = self.client.delete(
            f"/api/identities/{identity_id}",
            params={"impact_revision": impact_response.json()["revision"]},
        )
        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)

        template_response = self.client.get(
            f"/api/outreach-templates/{template_id}",
        )
        self.assertEqual(template_response.status_code, 200, msg=template_response.text)
        self.assertEqual(template_response.json()["id"], template_id)

        replacement_payload = self._build_identity_payload(
            with_imap=False,
            outreach_template_subject="新身份主题",
            outreach_template_body_text="新身份正文",
            outreach_template_body_html="<p>新身份正文</p>",
        )
        replacement_payload["name"] = "替代身份"
        replacement_payload["email_address"] = "replacement-sender@example.com"
        replacement_response = self.client.post(
            "/api/identities",
            json=replacement_payload,
        )
        self.assertEqual(
            replacement_response.status_code,
            201,
            msg=replacement_response.text,
        )
        self.assertNotEqual(replacement_response.json()["id"], identity_id)
        self.assertEqual(len(self.client.get("/api/outreach-templates").json()), 2)

    def test_identity_delete_rechecks_references_and_preserves_business_history(
        self,
    ) -> None:
        identity_id = self._create_identity(
            with_imap=False,
            email_address="identity-delete-guard@example.com",
        )
        llm_id = self._create_llm(name="身份删除保护模型")
        professor_id = self._create_professor(
            email="identity-delete-guard-professor@example.edu"
        )
        initial_impact = self.client.get(
            f"/api/identities/{identity_id}/deletion-impact"
        )
        self.assertEqual(initial_impact.status_code, 200, msg=initial_impact.text)
        self.assertTrue(initial_impact.json()["can_delete"])

        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="sent",
            primary_material_id=None,
        )
        stale_delete = self.client.delete(
            f"/api/identities/{identity_id}",
            params={"impact_revision": initial_impact.json()["revision"]},
        )
        self.assertEqual(stale_delete.status_code, 409, msg=stale_delete.text)
        self.assertEqual(
            stale_delete.json()["detail"]["code"],
            "IDENTITY_DELETE_PLAN_STALE",
        )

        current_impact = self.client.get(
            f"/api/identities/{identity_id}/deletion-impact"
        )
        self.assertTrue(current_impact.json()["can_delete"])
        self.assertEqual(current_impact.json()["references"]["email_tasks"], 1)
        retired = self.client.delete(
            f"/api/identities/{identity_id}",
            params={"impact_revision": current_impact.json()["revision"]},
        )
        self.assertEqual(retired.status_code, 204, msg=retired.text)
        self.assertNotIn(
            identity_id,
            [item["id"] for item in self.client.get("/api/identities").json()],
        )
        connection = sqlite3.connect(self.db_path)
        try:
            task_count = connection.execute(
                "SELECT COUNT(*) FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()[0]
            retired_identity = connection.execute(
                "SELECT deleted_at, smtp_password FROM identity_profiles WHERE id = ?",
                (identity_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(task_count, 1)
        self.assertIsNotNone(retired_identity[0])
        self.assertEqual(retired_identity[1], "")

    def test_identity_retirement_cancels_scheduled_delivery_and_allows_email_reuse(
        self,
    ) -> None:
        retired_email = "retired-email-reuse@example.com"
        identity_id = self._create_identity(
            with_imap=False,
            email_address=retired_email,
        )
        replacement_default_id = self._create_identity(
            with_imap=False,
            email_address="replacement-default@example.com",
        )
        llm_id = self._create_llm(name="身份删除自动取消模型")
        professor_id = self._create_professor(
            email="identity-retire-scheduled@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="scheduled",
            primary_material_id=None,
        )
        impact = self.client.get(f"/api/identities/{identity_id}/deletion-impact")
        self.assertEqual(impact.status_code, 200, msg=impact.text)
        self.assertTrue(impact.json()["can_delete"])
        self.assertEqual(
            impact.json()["automatic_actions"]["cancel_email_task_ids"],
            [task_id],
        )

        retired = self.client.delete(
            f"/api/identities/{identity_id}",
            params={"impact_revision": impact.json()["revision"]},
        )

        self.assertEqual(retired.status_code, 204, msg=retired.text)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            identity_row = connection.execute(
                """
                SELECT deleted_at, smtp_password, imap_password, is_default
                FROM identity_profiles WHERE id = ?
                """,
                (identity_id,),
            ).fetchone()
            task_row = connection.execute(
                "SELECT status, cancellation_reason FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        self.assertIsNotNone(identity_row[0])
        self.assertEqual(identity_row[1:], ("", None, 0))
        self.assertEqual(task_row, ("canceled", "identity_retired"))
        active_identities = self.client.get("/api/identities").json()
        self.assertNotIn(identity_id, [item["id"] for item in active_identities])
        replacement_default = next(
            item for item in active_identities if item["id"] == replacement_default_id
        )
        self.assertTrue(replacement_default["is_default"])

        recreated_payload = self._build_identity_payload(with_imap=False)
        recreated_payload["email_address"] = retired_email
        recreated_payload["name"] = "同邮箱新身份"
        recreated = self.client.post("/api/identities", json=recreated_payload)
        self.assertEqual(recreated.status_code, 201, msg=recreated.text)
        self.assertNotEqual(recreated.json()["id"], identity_id)

    def test_identity_retirement_reports_sending_task_id_and_keeps_identity_active(
        self,
    ) -> None:
        identity_id = self._create_identity(
            with_imap=False,
            email_address="identity-retire-sending@example.com",
        )
        llm_id = self._create_llm(name="身份删除发送保护模型")
        professor_id = self._create_professor(
            email="identity-retire-sending@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="sending",
            primary_material_id=None,
        )
        impact = self.client.get(f"/api/identities/{identity_id}/deletion-impact")
        self.assertEqual(impact.status_code, 200, msg=impact.text)
        self.assertFalse(impact.json()["can_delete"])
        self.assertEqual(
            impact.json()["blockers"][0]["entity_ids"],
            [task_id],
        )
        self.assertEqual(
            impact.json()["blockers"][0]["surface"],
            "任务中心 > 发送计划",
        )

        blocked = self.client.delete(
            f"/api/identities/{identity_id}",
            params={"impact_revision": impact.json()["revision"]},
        )

        self.assertEqual(blocked.status_code, 409, msg=blocked.text)
        self.assertEqual(blocked.json()["detail"]["code"], "IDENTITY_DELETE_BLOCKED")
        self.assertIn(str(task_id), blocked.json()["detail"]["message"])
        self.assertIn(
            identity_id,
            [item["id"] for item in self.client.get("/api/identities").json()],
        )

    def test_test_compose_generates_from_explicit_template_and_keeps_provenance(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        template_response = self.client.post(
            "/api/outreach-templates",
            json={
                "name": "测试写信固定模板",
                "recommended_generation_mode": "template",
                "subject": "测试主题 {{name}}",
                "body_text": "测试正文 {{sender_name}}",
                "body_html": "<p>测试正文 {{sender_name}}</p>",
            },
        )
        self.assertEqual(template_response.status_code, 201, msg=template_response.text)
        template_id = template_response.json()["id"]

        generate_response = self.client.post(
            f"/api/test-compose/{identity_id}/{llm_id}/generate-draft",
            json={
                "outreach_template_id": template_id,
                "subject": "本次测试主题 {{name}}",
                "body_text": "本次测试正文 {{sender_name}}",
                "body_html": "<p>本次测试正文 {{sender_name}}</p>",
            },
        )
        self.assertEqual(generate_response.status_code, 200, msg=generate_response.text)
        draft = generate_response.json()["draft"]
        self.assertEqual(draft["outreach_template_id"], template_id)
        self.assertEqual(draft["subject"], "本次测试主题 {{name}}")
        self.assertEqual(draft["body_text"], "本次测试正文 {{sender_name}}")

        archive_response = self.client.delete(
            f"/api/outreach-templates/{template_id}",
        )
        self.assertEqual(archive_response.status_code, 200, msg=archive_response.text)
        save_response = self.client.post(
            f"/api/test-compose/{identity_id}/{llm_id}/draft",
            json={
                "outreach_template_id": template_id,
                "subject": draft["subject"],
                "body_text": draft["body_text"],
                "body_html": draft["body_html"],
                "selected_material_ids": [],
            },
        )
        self.assertEqual(save_response.status_code, 200, msg=save_response.text)
        self.assertEqual(
            save_response.json()["draft"]["outreach_template_id"],
            template_id,
        )

    def test_material_and_mode_changes_reject_generating_rewrite(self) -> None:
        task_id = self._create_generating_workspace_rewrite_task()
        connection = sqlite3.connect(self.db_path)
        try:
            identity_id, primary_material_id = connection.execute(
                "SELECT identity_id, primary_material_id FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        finally:
            connection.close()
        second_material_id = self._upload_material(
            identity_id,
            filename="second-resume.txt",
            content=b"Another background in AI systems.",
            material_type="resume",
        )
        self.assertNotEqual(second_material_id, primary_material_id)

        material_response = self.client.post(
            f"/api/email-tasks/{task_id}/primary-material",
            json={"primary_material_id": second_material_id},
        )
        mode_response = self.client.post(
            f"/api/email-tasks/{task_id}/outreach-config",
            json={"outreach_generation_mode": "template"},
        )

        self.assertEqual(material_response.status_code, 400, msg=material_response.text)
        self.assertEqual(mode_response.status_code, 400, msg=mode_response.text)
        self.assertEqual(
            material_response.json()["detail"],
            "AI 正在改写当前草稿，请等待完成后再修改。",
        )
        self.assertEqual(
            mode_response.json()["detail"], "AI 正在改写当前草稿，请等待完成后再修改。"
        )
        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT primary_material_id, outreach_generation_mode FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0], primary_material_id)
        self.assertEqual(row[1], "llm")

    def test_material_upload_open_download_set_primary_and_delete(self) -> None:
        identity_id = self._create_identity(with_imap=False)

        resume_material_id = self._upload_material(
            identity_id,
            filename="cv.txt",
            content=b"My research background is in information extraction.",
            material_type="resume",
        )
        image_material_id = self._upload_material(
            identity_id,
            filename="poster.png",
            content=b"fake-image",
            material_type="portfolio",
        )

        identities = self.client.get("/api/identities").json()
        identity = next(item for item in identities if item["id"] == identity_id)
        self.assertEqual(identity["current_primary_material_id"], resume_material_id)
        self.assertEqual(len(identity["materials"]), 2)

        invalid_primary_response = self.client.post(
            f"/api/materials/{image_material_id}/set-primary"
        )
        self.assertEqual(invalid_primary_response.status_code, 400)

        open_response = self.client.get(f"/api/materials/{resume_material_id}/open")
        self.assertEqual(open_response.status_code, 200)
        self.assertIn("inline", open_response.headers.get("content-disposition", ""))

        download_response = self.client.get(
            f"/api/materials/{resume_material_id}/download"
        )
        self.assertEqual(download_response.status_code, 200)
        self.assertIn(
            "cv.txt", download_response.headers.get("content-disposition", "")
        )

        delete_primary_response = self.client.delete(
            f"/api/materials/{resume_material_id}"
        )
        self.assertEqual(delete_primary_response.status_code, 204)

        identity_after_primary_delete = next(
            item
            for item in self.client.get("/api/identities").json()
            if item["id"] == identity_id
        )
        self.assertIsNone(identity_after_primary_delete["current_primary_material_id"])
        self.assertEqual(len(identity_after_primary_delete["materials"]), 1)

        delete_response = self.client.delete(f"/api/materials/{image_material_id}")
        self.assertEqual(delete_response.status_code, 204)

        refreshed_identity = next(
            item
            for item in self.client.get("/api/identities").json()
            if item["id"] == identity_id
        )
        self.assertEqual(len(refreshed_identity["materials"]), 0)

    def test_global_material_is_reusable_and_survives_source_identity_deletion(
        self,
    ) -> None:
        source_identity_id = self._create_identity(
            email_address="material-source@example.com",
            with_imap=False,
        )
        target_identity_id = self._create_identity(
            email_address="material-target@example.com",
            with_imap=False,
        )
        material_id = self._upload_material(
            source_identity_id,
            filename="shared-resume.txt",
            content=b"Shared global resume",
            material_type="resume",
        )
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            material_path = Path(
                connection.execute(
                    "SELECT file_path FROM identity_materials WHERE id = ?",
                    (material_id,),
                ).fetchone()[0],
            )

        target_default_response = self.client.post(
            f"/api/identities/{target_identity_id}/materials/{material_id}/set-primary",
        )
        self.assertEqual(
            target_default_response.status_code, 200, msg=target_default_response.text
        )
        self.assertEqual(
            target_default_response.json()["default_for_identity_ids"],
            [source_identity_id, target_identity_id],
        )

        target_catalog = self.client.get(
            "/api/materials",
            params={"identity_id": target_identity_id},
        )
        self.assertEqual(target_catalog.status_code, 200, msg=target_catalog.text)
        shared = next(
            item for item in target_catalog.json() if item["id"] == material_id
        )
        self.assertTrue(shared["is_primary"])
        self.assertEqual(
            shared["default_for_identity_ids"],
            [source_identity_id, target_identity_id],
        )

        deletion_impact = self.client.get(
            f"/api/identities/{source_identity_id}/deletion-impact"
        )
        self.assertEqual(
            deletion_impact.status_code,
            200,
            msg=deletion_impact.text,
        )
        delete_source = self.client.delete(
            f"/api/identities/{source_identity_id}",
            params={"impact_revision": deletion_impact.json()["revision"]},
        )
        self.assertEqual(delete_source.status_code, 204, msg=delete_source.text)

        surviving_catalog = self.client.get("/api/materials").json()
        surviving = next(
            item for item in surviving_catalog if item["id"] == material_id
        )
        self.assertEqual(surviving["source_identity_id"], source_identity_id)
        self.assertEqual(surviving["default_for_identity_ids"], [target_identity_id])
        target_identity = next(
            item
            for item in self.client.get("/api/identities").json()
            if item["id"] == target_identity_id
        )
        self.assertEqual(target_identity["current_primary_material_id"], material_id)
        self.assertTrue(material_path.exists())

    def test_global_material_can_be_uploaded_without_an_identity(self) -> None:
        upload = self.client.post(
            "/api/materials",
            files={"file": ("global-note.png", b"global image", "image/png")},
            data={"material_type": "other", "display_name": "全局说明"},
        )
        self.assertEqual(upload.status_code, 201, msg=upload.text)
        material = upload.json()
        self.assertIsNone(material["source_identity_id"])
        self.assertFalse(material["is_primary"])

        identity_id = self._create_identity(with_imap=False)
        set_default = self.client.post(
            f"/api/identities/{identity_id}/materials/{material['id']}/set-primary",
        )
        self.assertEqual(set_default.status_code, 400, msg=set_default.text)

        reusable_upload = self.client.post(
            "/api/materials",
            files={"file": ("global-resume.txt", b"global resume", "text/plain")},
            data={"material_type": "resume", "display_name": "全局简历"},
        )
        self.assertEqual(reusable_upload.status_code, 201, msg=reusable_upload.text)
        reusable_id = reusable_upload.json()["id"]
        set_default = self.client.post(
            f"/api/identities/{identity_id}/materials/{reusable_id}/set-primary",
        )
        self.assertEqual(set_default.status_code, 200, msg=set_default.text)

    def test_delete_global_material_cleans_cross_identity_references(self) -> None:
        source_identity_id = self._create_identity(
            email_address="global-delete-source@example.com",
            with_imap=False,
        )
        target_identity_id = self._create_identity(
            email_address="global-delete-target@example.com",
            with_imap=False,
        )
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="global-delete@example.edu")
        material_id = self._upload_material(
            source_identity_id,
            filename="global-delete-resume.txt",
            content=b"global delete resume",
            material_type="resume",
        )
        set_default = self.client.post(
            f"/api/identities/{target_identity_id}/materials/{material_id}/set-primary",
        )
        self.assertEqual(set_default.status_code, 200, msg=set_default.text)
        task_id = self._insert_email_task_with_material(
            identity_id=target_identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="discovered",
            primary_material_id=material_id,
            selected_material_ids=[material_id],
        )
        rewrite_snapshot_professor_id = self._create_professor(
            email="global-delete-rewrite-snapshot@example.edu",
        )
        rewrite_snapshot_task_id = self._insert_email_task_with_material(
            identity_id=target_identity_id,
            llm_id=llm_id,
            professor_id=rewrite_snapshot_professor_id,
            status="approved",
            primary_material_id=None,
            selected_material_ids=[],
        )
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                """
                UPDATE email_tasks
                SET draft_rewrite_source_selected_material_ids = ?
                WHERE id = ?
                """,
                (json.dumps([str(material_id), 999999]), rewrite_snapshot_task_id),
            )
            connection.commit()

        deleted = self.client.delete(f"/api/materials/{material_id}")
        self.assertEqual(deleted.status_code, 204, msg=deleted.text)
        primary_material_id, selected_material_ids = self._get_task_material_references(
            task_id
        )
        self.assertIsNone(primary_material_id)
        self.assertEqual(selected_material_ids, [])
        identities = {
            identity["id"]: identity
            for identity in self.client.get("/api/identities").json()
        }
        self.assertIsNone(identities[source_identity_id]["current_primary_material_id"])
        self.assertIsNone(identities[target_identity_id]["current_primary_material_id"])
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            rewrite_snapshot = connection.execute(
                """
                SELECT draft_rewrite_source_selected_material_ids
                FROM email_tasks WHERE id = ?
                """,
                (rewrite_snapshot_task_id,),
            ).fetchone()[0]
        self.assertEqual(json.loads(rewrite_snapshot), [999999])

    def test_delete_global_material_is_blocked_by_another_identity_active_task(
        self,
    ) -> None:
        source_identity_id = self._create_identity(
            email_address="global-block-source@example.com",
            with_imap=False,
        )
        target_identity_id = self._create_identity(
            email_address="global-block-target@example.com",
            with_imap=False,
        )
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="global-block@example.edu")
        material_id = self._upload_material(
            source_identity_id,
            filename="global-block-resume.txt",
            content=b"global block resume",
            material_type="resume",
        )
        blocked_task_id = self._insert_email_task_with_material(
            identity_id=target_identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="approved",
            primary_material_id=material_id,
        )

        blocked = self.client.delete(f"/api/materials/{material_id}")
        self.assertEqual(blocked.status_code, 409, msg=blocked.text)
        self.assertEqual(blocked.json()["detail"]["code"], "MATERIAL_DELETION_BLOCKED")
        self.assertEqual(
            blocked.json()["detail"]["details"]["blockers"][0],
            {
                "kind": "email_task",
                "id": blocked_task_id,
                "status": "approved",
                "batch_task_id": None,
            },
        )
        self.assertTrue(
            any(
                item["id"] == material_id
                for item in self.client.get("/api/materials").json()
            )
        )

    def test_delete_material_detaches_discovered_and_matched_primary_material_references(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research background is in information extraction.",
            material_type="resume",
        )
        discovered_professor_id = self._create_professor(
            email="discovered-material-delete@example.edu"
        )
        matched_professor_id = self._create_professor(
            email="matched-material-delete@example.edu"
        )
        discovered_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=discovered_professor_id,
            status="discovered",
            primary_material_id=material_id,
        )
        matched_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=matched_professor_id,
            status="matched",
            primary_material_id=material_id,
            match_score=82,
            match_reason="方向匹配",
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        self.assertEqual(
            self._get_email_task_delete_state(discovered_task_id)["status"],
            "discovered",
        )
        self.assertEqual(
            self._get_email_task_delete_state(matched_task_id)["status"], "matched"
        )
        self.assertIsNone(self._get_task_material_references(discovered_task_id)[0])
        self.assertIsNone(self._get_task_material_references(matched_task_id)[0])

    def test_delete_material_detaches_match_analysis_run_primary_material_reference(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research background is in information extraction.",
            material_type="resume",
        )
        professor_id = self._create_professor(email="run-material-delete@example.edu")
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="matched",
            primary_material_id=material_id,
            match_score=82,
            match_reason="方向匹配",
        )
        run_id = self._insert_match_analysis_run(
            task_id=task_id,
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            primary_material_id=material_id,
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        self.assertIsNone(self._get_match_analysis_run_primary_material_id(run_id))

    def test_delete_material_removes_review_required_attachment_and_requires_review(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        deleted_material_id = self._upload_material(
            identity_id,
            filename="portfolio.pdf",
            content=b"portfolio",
            material_type="portfolio",
        )
        remaining_material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"resume",
            material_type="resume",
        )
        professor_id = self._create_professor(
            email="review-attachment-delete@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=None,
            selected_material_ids=[deleted_material_id, remaining_material_id],
            generated_subject="保留草稿主题",
            generated_content_text="保留草稿正文",
            generated_content_html="<p>保留草稿正文</p>",
            approved_subject="清空审核主题",
            approved_body_text="清空审核正文",
            approved_body_html="<p>清空审核正文</p>",
        )

        delete_response = self.client.delete(f"/api/materials/{deleted_material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        state = self._get_email_task_delete_state(task_id)
        self.assertEqual(state["status"], "review_required")
        self.assertEqual(state["selected_material_ids"], [remaining_material_id])
        self.assertEqual(state["generated_subject"], "保留草稿主题")
        self.assertIsNone(state["approved_subject"])
        self.assertIsNone(state["approved_body_text"])
        self.assertIsNone(state["approved_body_html"])
        self.assertIsNone(state["approved_at"])

    def test_delete_material_resets_send_failed_primary_material(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"resume",
            material_type="resume",
        )
        professor_id = self._create_professor(
            email="send-failed-primary-delete@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="send_failed",
            primary_material_id=material_id,
            generated_subject="旧主题",
            generated_content_text="旧正文",
            generated_content_html="<p>旧正文</p>",
            approved_subject="发送失败主题",
            approved_body_text="发送失败正文",
            approved_body_html="<p>发送失败正文</p>",
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        state = self._get_email_task_delete_state(task_id)
        self.assertEqual(state["status"], "discovered")
        self.assertIsNone(state["primary_material_id"])
        self.assertIsNone(state["generated_subject"])
        self.assertIsNone(state["approved_subject"])
        self.assertIsNone(state["last_error"])

    def test_delete_material_turns_send_failed_attachment_into_review_required(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        deleted_material_id = self._upload_material(
            identity_id,
            filename="attachment.pdf",
            content=b"attachment",
            material_type="portfolio",
        )
        professor_id = self._create_professor(
            email="send-failed-attachment-delete@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="send_failed",
            primary_material_id=None,
            selected_material_ids=[deleted_material_id],
            generated_subject="保留主题",
            generated_content_text="保留正文",
            generated_content_html="<p>保留正文</p>",
            approved_subject="清空主题",
            approved_body_text="清空正文",
            approved_body_html="<p>清空正文</p>",
        )

        delete_response = self.client.delete(f"/api/materials/{deleted_material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        state = self._get_email_task_delete_state(task_id)
        self.assertEqual(state["status"], "review_required")
        self.assertEqual(state["selected_material_ids"], [])
        self.assertEqual(state["generated_subject"], "保留主题")
        self.assertIsNone(state["approved_subject"])

    def test_delete_material_clears_send_failed_primary_material_reference(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research background is in information extraction.",
            material_type="resume",
        )
        professor_id = self._create_professor(
            email="send-failed-material-delete@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="send_failed",
            primary_material_id=material_id,
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        primary_material_id, selected_material_ids = self._get_task_material_references(
            task_id
        )
        self.assertIsNone(primary_material_id)
        self.assertIsNone(selected_material_ids)

    def test_delete_material_removes_failed_task_selected_material_reference(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        deleted_material_id = self._upload_material(
            identity_id,
            filename="portfolio.pdf",
            content=b"Portfolio content",
            material_type="portfolio",
        )
        remaining_material_id = self._upload_material(
            identity_id,
            filename="transcript.pdf",
            content=b"Transcript content",
            material_type="transcript",
        )
        professor_id = self._create_professor(
            email="failed-selected-material-delete@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="draft_failed",
            primary_material_id=None,
            selected_material_ids=[deleted_material_id, remaining_material_id],
        )

        delete_response = self.client.delete(f"/api/materials/{deleted_material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        primary_material_id, selected_material_ids = self._get_task_material_references(
            task_id
        )
        self.assertIsNone(primary_material_id)
        self.assertEqual(selected_material_ids, [remaining_material_id])

    def test_delete_material_does_not_partially_detach_when_blocked_task_exists(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"resume",
            material_type="resume",
        )
        detachable_professor_id = self._create_professor(
            email="detachable-blocked-delete@example.edu"
        )
        blocked_professor_id = self._create_professor(
            email="approved-blocked-delete@example.edu"
        )
        detachable_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=detachable_professor_id,
            status="matched",
            primary_material_id=material_id,
        )
        blocked_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=blocked_professor_id,
            status="approved",
            primary_material_id=material_id,
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 409)
        detail = delete_response.json()["detail"]
        self.assertEqual(detail["code"], "MATERIAL_DELETION_BLOCKED")
        self.assertEqual(detail["details"]["blockers"][0]["id"], blocked_task_id)
        self.assertEqual(
            self._get_task_material_references(detachable_task_id)[0], material_id
        )
        self.assertEqual(
            self._get_task_material_references(blocked_task_id)[0], material_id
        )

    def test_delete_material_clears_primary_and_attachment_reference_together(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"resume",
            material_type="resume",
        )
        professor_id = self._create_professor(
            email="primary-and-attachment-delete@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=material_id,
            selected_material_ids=[material_id],
            generated_subject="旧主题",
            generated_content_text="旧正文",
            generated_content_html="<p>旧正文</p>",
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)
        state = self._get_email_task_delete_state(task_id)
        self.assertEqual(state["status"], "discovered")
        self.assertIsNone(state["primary_material_id"])
        self.assertEqual(state["selected_material_ids"], [])
        self.assertIsNone(state["generated_subject"])

    def test_delete_material_still_blocks_active_primary_material_reference(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research background is in information extraction.",
            material_type="resume",
        )
        professor_id = self._create_professor(
            email="active-primary-material-delete@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="approved",
            primary_material_id=material_id,
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 409)
        detail = delete_response.json()["detail"]
        self.assertEqual(detail["code"], "MATERIAL_DELETION_BLOCKED")
        self.assertEqual(detail["details"]["blockers"][0]["id"], task_id)
        primary_material_id, selected_material_ids = self._get_task_material_references(
            task_id
        )
        self.assertEqual(primary_material_id, material_id)
        self.assertIsNone(selected_material_ids)

    def test_delete_material_still_blocks_active_selected_material_reference(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="portfolio.pdf",
            content=b"Portfolio content",
            material_type="portfolio",
        )
        professor_id = self._create_professor(
            email="active-selected-material-delete@example.edu"
        )
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="approved",
            primary_material_id=None,
            selected_material_ids=[material_id],
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 409)
        detail = delete_response.json()["detail"]
        self.assertEqual(detail["code"], "MATERIAL_DELETION_BLOCKED")
        self.assertEqual(detail["details"]["blockers"][0]["id"], task_id)
        primary_material_id, selected_material_ids = self._get_task_material_references(
            task_id
        )
        self.assertIsNone(primary_material_id)
        self.assertEqual(selected_material_ids, [material_id])

    def test_material_upload_defers_structured_text_extraction(self) -> None:
        identity_id = self._create_identity(with_imap=False)

        with patch(
            "app.services.file_storage._extract_text_with_structured_converter",
        ) as mocked_extractor:
            response = self.client.post(
                f"/api/identities/{identity_id}/materials",
                files={
                    "file": (
                        "transcript.pdf",
                        b"%PDF-pretend-transcript",
                        "application/pdf",
                    )
                },
                data={"material_type": "transcript"},
            )

        self.assertEqual(response.status_code, 201, msg=response.text)
        mocked_extractor.assert_not_called()
        body = response.json()
        self.assertEqual(body["material_type"], "transcript")
        self.assertEqual(body["display_name"], "transcript")

    def test_match_analysis_jobs_list_is_identity_scoped_not_llm_scoped(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        first_llm_id = self._create_llm()
        second_llm_response = self.client.post(
            "/api/llm-profiles",
            json={
                "name": "匹配分析备用模型",
                "provider": "openai",
                "api_base_url": "https://api-backup.example.com/v1",
                "api_key": "sk-test-backup",
                "model_name": "gpt-backup",
                "matcher_prompt_template": "matcher",
                "writer_prompt_template": "writer",
                "temperature": 0.2,
                "max_tokens": 2048,
                "is_default": False,
            },
        )
        self.assertEqual(
            second_llm_response.status_code, 201, msg=second_llm_response.text
        )
        second_llm_id = second_llm_response.json()["id"]
        self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"AI systems",
            material_type="resume",
        )
        professor_id = self._create_professor(email="match-switch-model@example.edu")

        created = self.client.post(
            "/api/match-analysis-jobs",
            json={
                "identity_id": identity_id,
                "llm_profile_id": first_llm_id,
                "professor_ids": [professor_id],
            },
        )
        self.assertEqual(created.status_code, 201, msg=created.text)

        listed = self.client.get(
            "/api/match-analysis-jobs",
            params={"identity_id": identity_id, "llm_profile_id": second_llm_id},
        )

        self.assertEqual(listed.status_code, 200, msg=listed.text)
        self.assertEqual([item["id"] for item in listed.json()], [created.json()["id"]])

    def test_calculate_match_uses_identity_primary_material_when_task_material_is_empty(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My background covers information extraction and agents.",
            material_type="resume",
        )
        set_primary_response = self.client.post(
            f"/api/materials/{material_id}/set-primary"
        )
        self.assertEqual(
            set_primary_response.status_code, 200, msg=set_primary_response.text
        )

        professor_response = self.client.post(
            "/api/professors",
            json={
                "name": "任务材料为空导师",
                "email": "empty-task-material-match@example.edu",
                "title": "Professor",
                "university": "Example University",
                "school": "School of AI",
                "department": "Computer Science",
                "research_direction": "Information extraction agents",
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
        task_id = ensure_response.json()["current_task"]["id"]

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "UPDATE email_tasks SET primary_material_id = NULL WHERE id = ?",
                (task_id,),
            )
            connection.commit()
        finally:
            connection.close()

        async def fake_generate_match_evaluation(**kwargs):
            self.assertEqual(kwargs["primary_material"].id, material_id)
            return self._build_match_evaluation_result(match_score=86)

        with patch(
            "app.modules.matching.task_analysis.llm_runtime.generate_match_evaluation",
            AsyncMock(side_effect=fake_generate_match_evaluation),
        ):
            response = self.client.post(f"/api/email-tasks/{task_id}/calculate-match")

        self.assertEqual(response.status_code, 200, msg=response.text)
        current_task = response.json()["thread"]["current_task"]
        self.assertEqual(current_task["match_score"], 86)
        self.assertEqual(current_task["primary_material_id"], material_id)

        connection = sqlite3.connect(self.db_path)
        try:
            stored_task_material_id = connection.execute(
                "SELECT primary_material_id FROM email_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()[0]
            run_material_id = connection.execute(
                "SELECT primary_material_id FROM match_analysis_runs WHERE email_task_id = ?",
                (task_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(stored_task_material_id, material_id)
        self.assertEqual(run_material_id, material_id)

    def test_email_task_approval_response_is_scoped_to_requested_task(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        professor_id = self._create_professor(email="scoped-email-task@example.edu")
        first_batch_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=None,
        )
        second_batch_id = self._insert_batch_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            status="running",
            primary_material_id=None,
        )
        first_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=None,
            batch_task_id=first_batch_id,
            source="batch",
            generated_subject="较早任务草稿",
            generated_content_text="较早任务正文",
            generated_content_html="<p>较早任务正文</p>",
        )
        second_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=None,
            batch_task_id=second_batch_id,
            source="batch",
            generated_subject="较新任务草稿",
            generated_content_text="较新任务正文",
            generated_content_html="<p>较新任务正文</p>",
        )

        response = self.client.post(
            f"/api/batch-tasks/{first_batch_id}/items/{first_task_id}/approve",
            json={
                "subject": "较早任务已审核",
                "body_text": "较早任务审核正文",
                "body_html": "<p>较早任务审核正文</p>",
                "selected_material_ids": [],
            },
        )

        self.assertEqual(response.status_code, 200, msg=response.text)
        self.assertEqual(response.json()["current_task"]["id"], first_task_id)
        self.assertEqual(
            response.json()["current_task"]["batch_task_id"], first_batch_id
        )
        self.assertEqual(response.json()["current_task"]["status"], "approved")
        second_state = self._get_email_task_delete_state(second_task_id)
        self.assertEqual(second_state["status"], "review_required")
        self.assertIsNone(second_state["approved_subject"])

    def test_test_compose_page_can_generate_and_send_to_self(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research focuses on information extraction and agents.",
            material_type="resume",
        )
        set_primary_response = self.client.post(
            f"/api/materials/{material_id}/set-primary"
        )
        self.assertEqual(
            set_primary_response.status_code, 200, msg=set_primary_response.text
        )

        with (
            patch(
                "app.modules.communications.test_compose.runtime.llm_runtime.generate_draft_content",
                AsyncMock(
                    return_value=self._build_draft_generation_result(
                        subject="测试主题",
                        body_text="测试正文",
                        body_html="<p>测试正文</p>",
                    ),
                ),
            ),
            patch(
                "app.modules.communications.test_compose.runtime.mail_runtime.send_email_to_recipient",
                AsyncMock(
                    return_value=self._build_send_result(
                        message_id="<self-test@example.com>",
                        provider_payload={"to": "sender@example.com"},
                    ),
                ),
            ) as mocked_send,
        ):
            thread_response = self.client.get(
                f"/api/test-compose/{identity_id}/{llm_id}"
            )
            draft_response = self.client.post(
                f"/api/test-compose/{identity_id}/{llm_id}/generate-draft"
            )
            send_response = self.client.post(
                f"/api/test-compose/{identity_id}/{llm_id}/send",
                json={
                    "subject": "测试主题",
                    "body_text": "测试正文",
                    "body_html": "<p>测试正文</p>",
                    "selected_material_ids": [],
                },
            )

        self.assertEqual(thread_response.status_code, 200, msg=thread_response.text)
        self.assertEqual(draft_response.status_code, 200, msg=draft_response.text)
        self.assertEqual(send_response.status_code, 200, msg=send_response.text)

        thread_payload = thread_response.json()
        draft_payload = draft_response.json()
        send_payload = send_response.json()

        self.assertEqual(thread_payload["draft"]["selected_material_ids"], [])
        self.assertEqual(draft_payload["draft"]["subject"], "测试主题")
        self.assertEqual(draft_payload["draft"]["body_text"], "测试正文")
        self.assertEqual(
            send_payload["history"][0]["recipient_email"], "sender@example.com"
        )
        self.assertEqual(send_payload["history"][0]["status"], "sent")
        self.assertEqual(
            send_payload["history"][0]["rfc_message_id"], "<self-test@example.com>"
        )
        mocked_send.assert_awaited_once()

    def test_delete_material_removes_stale_test_compose_attachment_selection(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()
        material_id = self._upload_material(
            identity_id,
            filename="resume.txt",
            content=b"My research background is in information extraction.",
            material_type="resume",
        )

        save_response = self.client.post(
            f"/api/test-compose/{identity_id}/{llm_id}/draft",
            json={
                "subject": "测试主题",
                "body_text": "测试正文",
                "body_html": "<p>测试正文</p>",
                "selected_material_ids": [material_id],
            },
        )
        self.assertEqual(save_response.status_code, 200, msg=save_response.text)
        self.assertEqual(
            save_response.json()["draft"]["selected_material_ids"], [material_id]
        )

        delete_response = self.client.delete(f"/api/materials/{material_id}")

        self.assertEqual(delete_response.status_code, 204, msg=delete_response.text)

        thread_response = self.client.get(f"/api/test-compose/{identity_id}/{llm_id}")

        self.assertEqual(thread_response.status_code, 200, msg=thread_response.text)
        payload = thread_response.json()
        self.assertEqual(payload["draft"]["selected_material_ids"], [])
        self.assertEqual(payload["material_options"], [])

    def test_test_compose_status_is_completed_by_identity_across_llm_profiles(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        first_llm_id = self._create_llm()
        second_llm_response = self.client.post(
            "/api/llm-profiles",
            json={
                "name": "备用模型",
                "provider": "openai",
                "api_base_url": "https://api-backup.example.com/v1",
                "api_key": "sk-test-backup",
                "model_name": "gpt-backup",
                "matcher_prompt_template": "matcher",
                "writer_prompt_template": "writer",
                "temperature": 0.2,
                "max_tokens": 2048,
                "is_default": False,
            },
        )
        self.assertEqual(
            second_llm_response.status_code, 201, msg=second_llm_response.text
        )

        with patch(
            "app.modules.communications.test_compose.runtime.mail_runtime.send_email_to_recipient",
            AsyncMock(
                return_value=self._build_send_result(
                    message_id="<identity-status@example.com>",
                    provider_payload={},
                ),
            ),
        ):
            send_response = self.client.post(
                f"/api/test-compose/{identity_id}/{first_llm_id}/send",
                json={
                    "subject": "测试主题",
                    "body_text": "测试正文",
                    "body_html": "<p>测试正文</p>",
                    "selected_material_ids": [],
                },
            )

        self.assertEqual(send_response.status_code, 200, msg=send_response.text)

        status_response = self.client.get(f"/api/test-compose/{identity_id}/status")

        self.assertEqual(status_response.status_code, 200, msg=status_response.text)
        self.assertTrue(status_response.json()["completed"])

    def test_test_compose_send_renders_placeholders_before_sending(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm()

        update_payload = self._build_identity_payload(
            with_imap=False,
            outreach_template_subject="测试给{{name}}",
            outreach_template_body_text="{{name}}您好，我是{{sender_name}}。",
            outreach_template_body_html="<p>{{name}}您好，我是{{sender_name}}。</p>",
        )
        update_payload["profile_name"] = "测试配置"
        update_payload["sender_name"] = "王同学"
        self.client.put(f"/api/identities/{identity_id}", json=update_payload)

        with patch(
            "app.modules.communications.test_compose.runtime.mail_runtime.send_email_to_recipient",
            AsyncMock(
                return_value=self._build_send_result(
                    message_id="<test-render@example.com>", provider_payload={}
                )
            ),
        ) as mocked_send:
            response = self.client.post(
                f"/api/test-compose/{identity_id}/{llm_id}/send",
                json={
                    "subject": "发送给{{name}}",
                    "body_text": "{{name}}您好，我是{{sender_name}}，研究方向：{{research_direction}}。",
                    "body_html": "<p>{{name}}您好，我是{{sender_name}}，研究方向：{{research_direction}}。</p>",
                    "selected_material_ids": [],
                },
            )

        self.assertEqual(response.status_code, 200, msg=response.text)
        kwargs = mocked_send.await_args.kwargs
        self.assertEqual(kwargs["recipient_name"], "测试收件人")
        self.assertEqual(kwargs["subject"], "发送给测试收件人")
        self.assertIn("测试收件人您好", kwargs["body_text"])
        self.assertIn("我是王同学", kwargs["body_text"])
        self.assertIn("测试研究方向", kwargs["body_text"])
        self.assertNotIn("{{name}}", kwargs["body_html"])

        history = response.json()["history"][0]
        self.assertEqual(history["subject"], "发送给测试收件人")
        self.assertIn("测试收件人您好", history["content"])
        self.assertNotIn("{{sender_name}}", history["content_html"])

        draft = response.json()["draft"]
        self.assertEqual(draft["subject"], "发送给{{name}}")
        self.assertIn("{{name}}您好", draft["body_text"])
        self.assertIn("{{sender_name}}", draft["body_text"])
        self.assertIn("{{name}}您好", draft["body_html"])
        self.assertIn("{{sender_name}}", draft["body_html"])

    def test_identity_missing_returns_utf8_detail_message(self) -> None:
        response = self.client.post("/api/identities/999999/smtp-test")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "未找到身份配置")

    def test_llm_profile_retirement_clears_credentials_and_allows_name_reuse(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm(name="可删除模型")

        impact = self.client.get(f"/api/llm-profiles/{llm_id}/deletion-impact")
        self.assertEqual(impact.status_code, 200, msg=impact.text)
        impact_payload = impact.json()
        self.assertTrue(impact_payload["can_delete"])
        self.assertTrue(
            any("发信模板不会删除" in warning for warning in impact_payload["warnings"])
        )
        self.assertTrue(
            any(
                "暂停或失败的任务不会自动继续" in warning
                for warning in impact_payload["warnings"]
            )
        )

        retired = self.client.delete(
            f"/api/llm-profiles/{llm_id}",
            params={"impact_revision": impact_payload["revision"]},
        )
        self.assertEqual(retired.status_code, 200, msg=retired.text)
        self.assertTrue(retired.json()["ok"])
        self.assertNotIn(
            llm_id,
            [profile["id"] for profile in self.client.get("/api/llm-profiles").json()],
        )

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            row = connection.execute(
                """
                SELECT api_key, api_base_url, matcher_prompt_template,
                       writer_prompt_template, is_default, deleted_at
                FROM llm_profiles
                WHERE id = ?
                """,
                (llm_id,),
            ).fetchone()
        self.assertEqual(row[:5], ("", None, None, None, 0))
        self.assertIsNotNone(row[5])

        identities = self.client.get("/api/identities")
        self.assertEqual(identities.status_code, 200, msg=identities.text)
        identity = next(item for item in identities.json() if item["id"] == identity_id)
        self.assertEqual(
            identity["outreach_template_subject"], "申请与{{name}}老师交流"
        )
        self.assertIn("老师您好", identity["outreach_template_body_text"])
        self.assertIsNotNone(identity["default_outreach_template_id"])
        templates = self.client.get("/api/outreach-templates")
        self.assertEqual(templates.status_code, 200, msg=templates.text)
        self.assertEqual(len(templates.json()), 1)

        recreated = self.client.post(
            "/api/llm-profiles",
            json={
                "name": "可删除模型",
                "provider": "openai",
                "api_base_url": "https://api.new.example/v1",
                "api_key": "sk-new-key",
                "model_name": "gpt-new",
                "matcher_prompt_template": None,
                "writer_prompt_template": None,
                "temperature": 0.2,
                "max_tokens": 2048,
                "is_default": False,
            },
        )
        self.assertEqual(recreated.status_code, 201, msg=recreated.text)
        self.assertNotEqual(recreated.json()["id"], llm_id)
        self.assertTrue(recreated.json()["is_default"])

    def test_llm_profile_retirement_preserves_history_and_non_llm_compose_actions(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm(name="历史模型")
        professor_id = self._create_professor(email="retired-history@example.edu")
        email_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="review_required",
            primary_material_id=None,
            generated_subject="历史主题",
            generated_content_text="历史正文",
            generated_content_html="<p>历史正文</p>",
            outreach_generation_mode="llm",
        )
        draft_payload = {
            "subject": "测试主题",
            "body_text": "测试正文",
            "body_html": "<p>测试正文</p>",
            "selected_material_ids": [],
        }
        saved = self.client.post(
            f"/api/test-compose/{identity_id}/{llm_id}/draft",
            json=draft_payload,
        )
        self.assertEqual(saved.status_code, 200, msg=saved.text)
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO agent_change_plans (
                    id, action, status, request_fingerprint, snapshot, expires_at
                )
                VALUES (
                    'retired-profile-plan', 'draft.generate',
                    'awaiting_confirmation', ?, ?, datetime('now', '+1 hour')
                )
                """,
                ("f" * 64, json.dumps({"request": {"llm_profile_id": llm_id}})),
            )
            connection.execute(
                """
                INSERT INTO agent_change_plans (
                    id, action, status, request_fingerprint, snapshot, expires_at
                )
                VALUES (
                    'unrelated-same-profile-id-plan', 'identity.update',
                    'awaiting_confirmation', ?, ?, datetime('now', '+1 hour')
                )
                """,
                ("e" * 64, json.dumps({"request": {"profile_id": llm_id}})),
            )
            connection.commit()

        impact = self.client.get(f"/api/llm-profiles/{llm_id}/deletion-impact").json()
        self.assertEqual(impact["references"]["email_tasks"], 1)
        self.assertEqual(impact["references"]["test_compose_sessions"], 1)
        self.assertEqual(impact["references"]["agent_change_plans"], 1)
        self.assertTrue(impact["can_delete"])
        retired = self.client.delete(
            f"/api/llm-profiles/{llm_id}",
            params={"impact_revision": impact["revision"]},
        )
        self.assertEqual(retired.status_code, 200, msg=retired.text)
        self.assertEqual(retired.json()["invalidated_plan_count"], 1)

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            task_profile_id = connection.execute(
                "SELECT llm_profile_id FROM email_tasks WHERE id = ?",
                (email_task_id,),
            ).fetchone()[0]
            compose_count = connection.execute(
                "SELECT COUNT(*) FROM test_compose_sessions WHERE llm_profile_id = ?",
                (llm_id,),
            ).fetchone()[0]
            plan_state = connection.execute(
                """
                SELECT status, failure_message
                FROM agent_change_plans
                WHERE id = 'retired-profile-plan'
                """
            ).fetchone()
            unrelated_plan_status = connection.execute(
                """
                SELECT status
                FROM agent_change_plans
                WHERE id = 'unrelated-same-profile-id-plan'
                """
            ).fetchone()[0]
        self.assertEqual(task_profile_id, llm_id)
        self.assertEqual(compose_count, 1)
        self.assertEqual(plan_state[0], "canceled")
        self.assertIn("模型配置已删除", plan_state[1])
        self.assertEqual(unrelated_plan_status, "awaiting_confirmation")

        task_thread = self.client.get(f"/api/email-tasks/{email_task_id}/thread")
        self.assertEqual(task_thread.status_code, 200, msg=task_thread.text)
        self.assertEqual(task_thread.json()["llm_profile"]["id"], llm_id)
        history = self.client.get(f"/api/test-compose/{identity_id}/{llm_id}")
        self.assertEqual(history.status_code, 200, msg=history.text)
        resaved = self.client.post(
            f"/api/test-compose/{identity_id}/{llm_id}/draft",
            json={**draft_payload, "subject": "删除后仍可保存"},
        )
        self.assertEqual(resaved.status_code, 200, msg=resaved.text)
        with patch(
            "app.modules.communications.test_compose.runtime.mail_runtime.send_email_to_recipient",
            AsyncMock(
                return_value=self._build_send_result(
                    message_id="<retired-profile@example.com>",
                    provider_payload={},
                )
            ),
        ):
            sent = self.client.post(
                f"/api/test-compose/{identity_id}/{llm_id}/send",
                json=draft_payload,
            )
        self.assertEqual(sent.status_code, 200, msg=sent.text)

        generate = self.client.post(
            f"/api/test-compose/{identity_id}/{llm_id}/generate-draft"
        )
        self.assertEqual(generate.status_code, 409, msg=generate.text)
        self.assertIn("已删除", generate.json()["detail"])

    def test_llm_profile_retirement_blocks_active_work_and_rejects_stale_plan(
        self,
    ) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm(name="运行中模型")
        professor_id = self._create_professor(email="retire-running@example.edu")
        task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="generating_draft",
            primary_material_id=None,
            outreach_generation_mode="llm",
        )

        impact = self.client.get(f"/api/llm-profiles/{llm_id}/deletion-impact").json()
        self.assertFalse(impact["can_delete"])
        self.assertEqual(impact["blockers"][0]["kind"], "draft_generation")
        self.assertIn(task_id, impact["blockers"][0]["entity_ids"])
        self.assertEqual(
            impact["blockers"][0]["surface"],
            "任务中心 > 发送计划或批量任务详情",
        )
        blocked = self.client.delete(
            f"/api/llm-profiles/{llm_id}",
            params={"impact_revision": impact["revision"]},
        )
        self.assertEqual(blocked.status_code, 409, msg=blocked.text)
        self.assertEqual(blocked.json()["detail"]["code"], "LLM_PROFILE_IN_USE")

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                "UPDATE email_tasks SET status = 'draft_failed' WHERE id = ?",
                (task_id,),
            )
            connection.commit()
        stale = self.client.delete(
            f"/api/llm-profiles/{llm_id}",
            params={"impact_revision": impact["revision"]},
        )
        self.assertEqual(stale.status_code, 409, msg=stale.text)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "LLM_PROFILE_DELETE_PLAN_STALE",
        )

        refreshed = self.client.get(
            f"/api/llm-profiles/{llm_id}/deletion-impact"
        ).json()
        self.assertTrue(refreshed["can_delete"])
        retired = self.client.delete(
            f"/api/llm-profiles/{llm_id}",
            params={"impact_revision": refreshed["revision"]},
        )
        self.assertEqual(retired.status_code, 200, msg=retired.text)

    def test_llm_profile_retirement_cancels_queued_future_work(self) -> None:
        identity_id = self._create_identity(with_imap=False)
        llm_id = self._create_llm(name="排队工作模型")
        professor_id = self._create_professor(email="retire-queued@example.edu")
        email_task_id = self._insert_email_task_with_material(
            identity_id=identity_id,
            llm_id=llm_id,
            professor_id=professor_id,
            status="discovered",
            primary_material_id=None,
            outreach_generation_mode="llm",
        )
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            batch_task_id = connection.execute(
                """
                INSERT INTO batch_tasks (
                    identity_id, llm_profile_id, name, status, target_count,
                    schedule_type, outreach_generation_mode
                )
                VALUES (?, ?, '待生成批次', 'running', 1, 'immediate', 'llm')
                RETURNING id
                """,
                (identity_id, llm_id),
            ).fetchone()[0]
            connection.execute(
                "UPDATE email_tasks SET batch_task_id = ?, source = 'batch' WHERE id = ?",
                (batch_task_id, email_task_id),
            )
            match_job_id = connection.execute(
                """
                INSERT INTO match_analysis_jobs (
                    name, identity_id, match_source_identity_id, llm_profile_id,
                    status, target_count
                ) VALUES ('待分析任务', ?, ?, ?, 'queued', 0)
                RETURNING id
                """,
                (identity_id, identity_id, llm_id),
            ).fetchone()[0]
            crawl_job_id = connection.execute(
                """
                INSERT INTO crawl_jobs (
                    university, school, start_url, llm_profile_id, status
                ) VALUES ('测试大学', '测试学院', 'https://example.edu', ?, 'queued')
                RETURNING id
                """,
                (llm_id,),
            ).fetchone()[0]
            connection.commit()

        impact = self.client.get(f"/api/llm-profiles/{llm_id}/deletion-impact").json()

        self.assertTrue(impact["can_delete"])
        self.assertEqual(impact["blockers"], [])
        self.assertEqual(
            impact["automatic_actions"],
            {
                "cancel_email_task_ids": [email_task_id],
                "cancel_match_analysis_job_ids": [match_job_id],
                "cancel_crawl_job_ids": [crawl_job_id],
            },
        )
        retired = self.client.delete(
            f"/api/llm-profiles/{llm_id}",
            params={"impact_revision": impact["revision"]},
        )
        self.assertEqual(retired.status_code, 200, msg=retired.text)
        self.assertEqual(retired.json()["canceled_email_task_ids"], [email_task_id])
        self.assertEqual(
            retired.json()["canceled_match_analysis_job_ids"],
            [match_job_id],
        )
        self.assertEqual(retired.json()["canceled_crawl_job_ids"], [crawl_job_id])
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            email_state = connection.execute(
                "SELECT status, cancellation_reason FROM email_tasks WHERE id = ?",
                (email_task_id,),
            ).fetchone()
            match_state = connection.execute(
                "SELECT status, cancel_requested_at FROM match_analysis_jobs WHERE id = ?",
                (match_job_id,),
            ).fetchone()
            crawl_state = connection.execute(
                "SELECT status FROM crawl_jobs WHERE id = ?",
                (crawl_job_id,),
            ).fetchone()[0]
            batch_state = connection.execute(
                "SELECT status, target_count FROM batch_tasks WHERE id = ?",
                (batch_task_id,),
            ).fetchone()
        self.assertEqual(email_state, ("canceled", "llm_profile_retired"))
        self.assertEqual(match_state[0], "canceled")
        self.assertIsNotNone(match_state[1])
        self.assertEqual(crawl_state, "canceled")
        self.assertEqual(batch_state, ("completed", 0))

    def test_llm_profile_retirement_blocks_interactive_model_requests(self) -> None:
        from app.modules.llm.usage import (
            begin_llm_profile_retirement,
            end_llm_profile_retirement,
            track_llm_profile_usage,
        )

        llm_id = self._create_llm(name="交互占用模型")
        with track_llm_profile_usage(llm_id, "connectivity_test"):
            impact = self.client.get(
                f"/api/llm-profiles/{llm_id}/deletion-impact"
            ).json()
            self.assertFalse(impact["can_delete"])
            self.assertEqual(
                impact["blockers"][0]["kind"],
                "interactive_connectivity_test",
            )
            blocked = self.client.delete(
                f"/api/llm-profiles/{llm_id}",
                params={"impact_revision": impact["revision"]},
            )
            self.assertEqual(blocked.status_code, 409, msg=blocked.text)
            self.assertEqual(
                blocked.json()["detail"]["code"],
                "LLM_PROFILE_IN_USE",
            )

        self.assertTrue(begin_llm_profile_retirement(llm_id))
        try:
            models = self.client.get(f"/api/llm-profiles/{llm_id}/models")
            tested = self.client.post(f"/api/llm-profiles/{llm_id}/test")
        finally:
            end_llm_profile_retirement(llm_id)
        for response in (models, tested):
            self.assertEqual(response.status_code, 409, msg=response.text)
            self.assertEqual(
                response.json()["detail"]["code"],
                "LLM_PROFILE_RETIRING",
            )

        refreshed = self.client.get(
            f"/api/llm-profiles/{llm_id}/deletion-impact"
        ).json()
        retired = self.client.delete(
            f"/api/llm-profiles/{llm_id}",
            params={"impact_revision": refreshed["revision"]},
        )
        self.assertEqual(retired.status_code, 200, msg=retired.text)

    def test_llm_profile_retirement_sanitizes_rebound_crawl_run_snapshots(
        self,
    ) -> None:
        retired_llm_id = self._create_llm(name="抓取历史旧模型")
        replacement_id = self._create_llm(name="抓取当前模型")
        snapshot = {
            "profile_id": retired_llm_id,
            "profile_name": "抓取历史旧模型",
            "provider": "openai",
            "api_base_url": "https://secret-endpoint.example/v1",
            "model_name": "gpt-old",
            "matcher_prompt_template": "private matcher prompt",
            "writer_prompt_template": "private writer prompt",
            "temperature": 0.2,
            "max_tokens": 2048,
        }
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            job_id = connection.execute(
                """
                INSERT INTO crawl_jobs (
                    university, school, start_url, llm_profile_id, status
                )
                VALUES ('测试大学', '测试学院', 'https://example.edu', ?, 'completed')
                RETURNING id
                """,
                (replacement_id,),
            ).fetchone()[0]
            run_id = connection.execute(
                """
                INSERT INTO crawl_job_runs (
                    job_id, attempt_number, status, llm_runtime_snapshot
                )
                VALUES (?, 1, 'completed', ?)
                RETURNING id
                """,
                (job_id, json.dumps(snapshot)),
            ).fetchone()[0]
            connection.commit()

        impact = self.client.get(
            f"/api/llm-profiles/{retired_llm_id}/deletion-impact"
        ).json()
        self.assertEqual(impact["references"]["crawl_jobs"], 1)
        self.assertEqual(impact["references"]["crawl_runs"], 1)
        retired = self.client.delete(
            f"/api/llm-profiles/{retired_llm_id}",
            params={"impact_revision": impact["revision"]},
        )
        self.assertEqual(retired.status_code, 200, msg=retired.text)

        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            stored_snapshot = json.loads(
                connection.execute(
                    "SELECT llm_runtime_snapshot FROM crawl_job_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()[0]
            )
        self.assertEqual(stored_snapshot["profile_id"], retired_llm_id)
        self.assertEqual(stored_snapshot["model_name"], "gpt-old")
        self.assertNotIn("api_base_url", stored_snapshot)
        self.assertNotIn("matcher_prompt_template", stored_snapshot)
        self.assertNotIn("writer_prompt_template", stored_snapshot)

    def test_llm_profile_retirement_blocks_active_crawl_run_snapshot(self) -> None:
        retired_llm_id = self._create_llm(name="抓取运行快照旧模型")
        replacement_id = self._create_llm(name="抓取运行快照当前模型")
        snapshot = {
            "profile_id": retired_llm_id,
            "profile_name": "抓取运行快照旧模型",
            "provider": "openai",
            "model_name": "gpt-old",
        }
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            job_id = connection.execute(
                """
                INSERT INTO crawl_jobs (
                    university, school, start_url, llm_profile_id, status
                )
                VALUES ('测试大学', '测试学院', 'https://example.edu', ?, 'running')
                RETURNING id
                """,
                (replacement_id,),
            ).fetchone()[0]
            run_id = connection.execute(
                """
                INSERT INTO crawl_job_runs (
                    job_id, attempt_number, status, llm_runtime_snapshot
                )
                VALUES (?, 1, 'running', ?)
                RETURNING id
                """,
                (job_id, json.dumps(snapshot)),
            ).fetchone()[0]
            connection.execute(
                "UPDATE crawl_jobs SET current_run_id = ? WHERE id = ?",
                (run_id, job_id),
            )
            connection.commit()

        impact = self.client.get(
            f"/api/llm-profiles/{retired_llm_id}/deletion-impact"
        ).json()
        self.assertFalse(impact["can_delete"])
        blocker = next(
            item for item in impact["blockers"] if item["kind"] == "crawl_job"
        )
        self.assertIn(job_id, blocker["entity_ids"])

        blocked = self.client.delete(
            f"/api/llm-profiles/{retired_llm_id}",
            params={"impact_revision": impact["revision"]},
        )
        self.assertEqual(blocked.status_code, 409, msg=blocked.text)
        self.assertEqual(blocked.json()["detail"]["code"], "LLM_PROFILE_IN_USE")

    def test_llm_profile_retirement_only_removes_unshared_adaptation_caches(
        self,
    ) -> None:
        base_url = "https://api.example.com/v1"
        model_name = "gpt-4o-mini"

        def seed_caches() -> None:
            with closing(sqlite3.connect(self.db_path)) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO llm_endpoint_adaptation_cache (
                        api_base_url, model_name, learned_endpoint_kind
                    ) VALUES (?, ?, 'chat_completions')
                    """,
                    (base_url, model_name),
                )
                connection.execute(
                    """
                    INSERT INTO thinking_adaptation_cache (
                        api_base_url, model_name, endpoint_kind, learned_extra_body
                    ) VALUES (?, ?, 'chat_completions', '{}')
                    """,
                    (base_url, model_name),
                )
                connection.execute(
                    """
                    INSERT INTO llm_structured_output_adaptation_cache (
                        api_base_url, model_name, endpoint_kind, probe_version,
                        learned_mode
                    ) VALUES (?, ?, 'chat_completions', 1, 'json_object')
                    """,
                    (base_url, model_name),
                )
                connection.commit()

        def cache_counts() -> tuple[int, int, int]:
            with closing(sqlite3.connect(self.db_path)) as connection, connection:
                return tuple(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in (
                        "llm_endpoint_adaptation_cache",
                        "thinking_adaptation_cache",
                        "llm_structured_output_adaptation_cache",
                    )
                )

        unshared_id = self._create_llm(name="独占缓存模型")
        seed_caches()
        impact = self.client.get(
            f"/api/llm-profiles/{unshared_id}/deletion-impact"
        ).json()
        retired = self.client.delete(
            f"/api/llm-profiles/{unshared_id}",
            params={"impact_revision": impact["revision"]},
        )
        self.assertEqual(retired.status_code, 200, msg=retired.text)
        self.assertEqual(cache_counts(), (0, 0, 0))

        shared_id = self._create_llm(name="共享缓存模型一")
        self._create_llm(name="共享缓存模型二")
        seed_caches()
        shared_impact = self.client.get(
            f"/api/llm-profiles/{shared_id}/deletion-impact"
        ).json()
        shared_retired = self.client.delete(
            f"/api/llm-profiles/{shared_id}",
            params={"impact_revision": shared_impact["revision"]},
        )
        self.assertEqual(shared_retired.status_code, 200, msg=shared_retired.text)
        self.assertEqual(cache_counts(), (1, 1, 1))

    def test_llm_profile_retirement_handles_default_replacement_explicitly(
        self,
    ) -> None:
        first_id = self._create_llm(name="默认替代模型")
        default_id = self._create_llm(name="待删除默认模型")
        impact = self.client.get(
            f"/api/llm-profiles/{default_id}/deletion-impact"
        ).json()
        self.assertTrue(impact["is_default"])

        retired = self.client.delete(
            f"/api/llm-profiles/{default_id}",
            params={
                "impact_revision": impact["revision"],
                "replacement_default_profile_id": first_id,
            },
        )
        self.assertEqual(retired.status_code, 200, msg=retired.text)
        self.assertEqual(retired.json()["default_profile_id"], first_id)
        active = self.client.get("/api/llm-profiles").json()
        self.assertTrue(
            next(item for item in active if item["id"] == first_id)["is_default"]
        )

        no_default_id = self._create_llm(name="删除后无默认")
        no_default_impact = self.client.get(
            f"/api/llm-profiles/{no_default_id}/deletion-impact"
        ).json()
        retired_without_replacement = self.client.delete(
            f"/api/llm-profiles/{no_default_id}",
            params={"impact_revision": no_default_impact["revision"]},
        )
        self.assertEqual(
            retired_without_replacement.status_code,
            200,
            msg=retired_without_replacement.text,
        )
        self.assertIsNone(retired_without_replacement.json()["default_profile_id"])
        self.assertFalse(
            any(
                item["is_default"]
                for item in self.client.get("/api/llm-profiles").json()
            )
        )

    def test_llm_profile_retirement_protects_default_replacement_until_commit(
        self,
    ) -> None:
        from app.modules.llm.usage import (
            begin_llm_profile_retirement,
            end_llm_profile_retirement,
        )

        replacement_id = self._create_llm(name="并发替代模型")
        default_id = self._create_llm(name="并发待删除默认模型")
        impact = self.client.get(
            f"/api/llm-profiles/{default_id}/deletion-impact"
        ).json()

        self.assertTrue(begin_llm_profile_retirement(replacement_id))
        try:
            blocked = self.client.delete(
                f"/api/llm-profiles/{default_id}",
                params={
                    "impact_revision": impact["revision"],
                    "replacement_default_profile_id": replacement_id,
                },
            )
        finally:
            end_llm_profile_retirement(replacement_id)

        self.assertEqual(blocked.status_code, 409, msg=blocked.text)
        self.assertEqual(
            blocked.json()["detail"]["code"],
            "LLM_PROFILE_DEFAULT_REPLACEMENT_UNAVAILABLE",
        )
        active_ids = {
            profile["id"] for profile in self.client.get("/api/llm-profiles").json()
        }
        self.assertIn(default_id, active_ids)
        self.assertIn(replacement_id, active_ids)

        retried = self.client.delete(
            f"/api/llm-profiles/{default_id}",
            params={
                "impact_revision": impact["revision"],
                "replacement_default_profile_id": replacement_id,
            },
        )
        self.assertEqual(retried.status_code, 200, msg=retried.text)
        self.assertEqual(retried.json()["default_profile_id"], replacement_id)
