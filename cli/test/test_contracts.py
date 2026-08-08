from __future__ import annotations

import json
import os
import stat
import unittest
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from auto_email_sender_cli.capabilities import (
    CAPABILITIES,
    capability_catalog_revision,
    capability_stateful,
    collection_filter_fields,
    supports_if_revision,
    supports_pagination,
    supports_wait,
)
from auto_email_sender_cli.contracts import command_contract_revision, validate_command_contract
from auto_email_sender_cli.describe import (
    compact_command_description,
    describe_command,
    describe_commands,
    describe_command_revisions,
)
from auto_email_sender_cli.errors import CliError
from auto_email_sender_cli.main import app
from auto_email_sender_cli.operation_specs import (
    OPERATION_SPECS,
    effect_has_external_action,
    validate_operation_manifest,
)
from auto_email_sender_cli.commands.common import (
    _redact_receipt_value,
    _with_revision,
    add_mutation_receipt,
    augment_state_metadata,
    apply_structured_filter,
    fetch_all_pages,
    normalize_collection_response,
    server_filter_params,
)
from auto_email_sender_cli.commands.wait import _available_actions


class _FakeClient:
    descriptor = type("Descriptor", (), {"app_version": "test"})()
    last_request_id = "request-test"
    last_response_headers: dict[str, str] = {}

    def request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
        return {
            "items": [
                {"id": 1, "name": "A", "email": "a@example.edu"},
                {"id": 2, "name": "B", "email": "b@example.edu"},
            ],
            "next_cursor": None,
            "has_more": False,
        }


class ContractTests(unittest.TestCase):
    def test_operation_manifest_covers_every_capability_without_legacy_drift(self) -> None:
        self.assertEqual(validate_operation_manifest(CAPABILITIES), [])
        self.assertEqual({item.command for item in CAPABILITIES}, set(OPERATION_SPECS))
        for capability in CAPABILITIES:
            spec = OPERATION_SPECS[capability.command]
            self.assertEqual(spec.effects.mutates, capability.mutates, capability.command)
            self.assertEqual(
                effect_has_external_action(spec.effects),
                capability.external_action,
                capability.command,
            )
            self.assertEqual(
                spec.effects.plan_role in {"producer", "consumer"},
                capability.requires_plan,
                capability.command,
            )
            self.assertEqual(
                spec.effects.requires_confirmation_plan,
                spec.effects.plan_role == "consumer",
                capability.command,
            )

    def test_contract_revision_hashes_full_parser_and_semantic_contract(self) -> None:
        description = describe_command(app, "plans.execute")
        assert description is not None
        revision = description["contract_revision"]
        self.assertIsInstance(revision, str)
        self.assertEqual(revision, command_contract_revision(description))

        changed = deepcopy(description)
        changed["effects"]["impact_scope"] = "different explicit scope"
        self.assertNotEqual(revision, command_contract_revision(changed))

        changed_input = deepcopy(description)
        changed_input["input"]["schema"]["properties"]["confirm"]["required"] = True
        self.assertNotEqual(revision, command_contract_revision(changed_input))

        guide = describe_command(app, "guide")
        assert guide is not None
        self.assertTrue(guide["lifecycle"]["deprecated"])
        self.assertEqual(guide["lifecycle"]["replaced_by"], ["capabilities", "describe"])

    def test_communication_group_contract_exposes_match_source_controls(self) -> None:
        create = describe_command(app, "communication-groups.create")
        update = describe_command(app, "communication-groups.update")
        get = describe_command(app, "communication-groups.get")
        self.assertIsNotNone(create)
        self.assertIsNotNone(update)
        self.assertIsNotNone(get)
        assert create is not None
        assert update is not None
        assert get is not None

        create_properties = create["input"]["schema"]["properties"]
        update_properties = update["input"]["schema"]["properties"]
        self.assertIn("match_source_identity_id", create_properties)
        self.assertIn("match_source_identity_id", update_properties)
        self.assertIn("clear_match_source_identity", update_properties)
        self.assertIn("match_source_identity_id", get["output"]["known_fields"])

    def test_catalog_revision_includes_each_live_command_contract_revision(self) -> None:
        commands = [
            capability.command
            for capability in CAPABILITIES
            if capability.availability == "available"
        ]
        revisions = describe_command_revisions(app, commands)
        self.assertEqual(set(revisions), set(commands))
        self.assertTrue(all(len(value) == 16 for value in revisions.values()))

        baseline = capability_catalog_revision(revisions)
        changed = dict(revisions)
        changed["plans.execute"] = "0" * 16
        self.assertNotEqual(baseline, capability_catalog_revision(changed))

    def test_versioned_baseline_lists_match_the_live_capability_registry(self) -> None:
        baseline_path = (
            Path(__file__).resolve().parents[2]
            / "docs"
            / "development"
            / "agent_cli_baseline.json"
        )
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        concurrency = json.loads(
            (baseline_path.parent / "agent_cli_concurrency_coverage.json").read_text(encoding="utf-8"),
        )
        gui = json.loads(
            (baseline_path.parent / "agent_cli_gui_coverage.json").read_text(encoding="utf-8"),
        )
        available = [item.command for item in CAPABILITIES if item.availability == "available"]
        writes = [item.command for item in CAPABILITIES if item.availability == "available" and item.mutates]
        high_risk = [
            item.command
            for item in CAPABILITIES
            if item.availability == "available" and item.risk_level in {"L2", "L3"}
        ]
        paged = [item.command for item in CAPABILITIES if item.availability == "available" and supports_pagination(item.command)]
        stateful = [item.command for item in CAPABILITIES if item.availability == "available" and capability_stateful(item.command)]
        self.assertEqual(baseline["available_leaf_commands"], available)
        self.assertEqual(baseline["write_commands"], writes)
        self.assertEqual(baseline["high_risk_commands"], high_risk)
        self.assertEqual(baseline["paged_collection_commands"], paged)
        self.assertEqual(baseline["stateful_commands"], stateful)
        self.assertEqual(baseline["counts"]["available_leaf_commands"], len(available))
        self.assertEqual(baseline["counts"]["write_commands"], len(writes))
        self.assertEqual(baseline["counts"]["high_risk_commands"], len(high_risk))
        self.assertEqual(baseline["counts"]["paged_collection_commands"], len(paged))
        self.assertEqual(baseline["counts"]["stateful_commands"], len(stateful))
        self.assertEqual(
            baseline["critical_concurrency_objects"],
            [item["resource"] for item in concurrency["objects"]],
        )
        self.assertEqual(
            baseline["gui_business_api_modules"],
            [item["source"] for item in gui["actions"]],
        )

    def test_every_available_leaf_has_a_complete_schema_validated_contract(self) -> None:
        failures: list[str] = []
        descriptions = describe_commands(
            app,
            (item.command for item in CAPABILITIES if item.availability == "available"),
        )
        for capability in CAPABILITIES:
            if capability.availability != "available":
                continue
            description = descriptions.get(capability.command)
            self.assertIsNotNone(description, capability.command)
            assert description is not None
            errors = validate_command_contract(description)
            if errors:
                failures.append(f"{capability.command}: {', '.join(errors)}")
            self.assertEqual(description["command"], capability.command)
            self.assertEqual(description["risk"]["level"], capability.risk_level)
            self.assertEqual(description["effects"]["mutates"], capability.mutates)
            self.assertEqual(description["risk"]["availability"], capability.availability)
            self.assertEqual(description["risk"]["requires_plan"], capability.requires_plan)
            compact = compact_command_description(description)
            self.assertLess(
                len(
                    json.dumps(
                        compact,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8"),
                ),
                1_501,
                capability.command,
            )
            self.assertEqual(
                description["effects"]["requires_confirmation_plan"],
                description["effects"]["plan_role"] == "consumer",
            )
        self.assertEqual(failures, [])

    def test_capabilities_and_describe_have_zero_metadata_drift(self) -> None:
        failures: list[str] = []
        descriptions = describe_commands(
            app,
            (item.command for item in CAPABILITIES if item.availability == "available"),
        )
        for capability in CAPABILITIES:
            if capability.availability != "available":
                continue
            description = descriptions.get(capability.command)
            assert description is not None
            advertised = capability.to_dict()
            checks = {
                "command": description["command"] == advertised["command"],
                "resource": description["resource"] == advertised["resource"],
                "risk": description["risk"]["level"] == advertised["risk_level"],
                "availability": description["risk"]["availability"] == advertised["availability"],
                "mutates": description["effects"]["mutates"] == advertised["mutates"],
                "plan_role": description["effects"]["plan_role"]
                == advertised["plan_role"],
                "confirmation_before_invocation": (
                    description["effects"]["confirmation_required_before_invocation"]
                    == advertised["confirmation_required_before_invocation"]
                ),
                "produces_plan": description["effects"]["produces_confirmation_plan"]
                == advertised["produces_confirmation_plan"],
                "long_running": description["risk"]["long_running"] == advertised["long_running"],
                "field_selection": description["output"]["field_selection"]
                == advertised["supports_field_selection"],
                "file_export": description["output"]["file_export"]
                == advertised["supports_file_export"],
                "structured_filter": description["output"]["structured_filter"]
                == advertised["supports_structured_filter"],
                "if_revision": description["input"]["global_options"]["if_revision"]["supported"]
                == advertised["supports_if_revision"],
                "filter_fields": description["output"]["filter_contract"]["fields"]
                is not None
                and set(description["output"]["filter_contract"]["fields"])
                == set(advertised["filter_fields"]),
                "filter_field_types": all(
                    isinstance(field_schema, dict)
                    and field_schema.get("type") not in (None, "unknown")
                    and set(field_schema.get("operators", [])) == set(advertised["filter_operators"])
                    for field_schema in description["output"]["filter_contract"]["fields"].values()
                ),
            }
            failures.extend(
                f"{capability.command}:{name}"
                for name, matched in checks.items()
                if not matched
            )
        self.assertEqual(failures, [])

    def test_bounded_community_comparison_reads_advertise_nested_field_selection(self) -> None:
        for command in ("professors.community.records", "professors.community.preview"):
            description = describe_command(app, command)
            assert description is not None
            self.assertTrue(description["output"]["field_selection"], command)
            self.assertFalse(description["output"]["pagination"], command)
            self.assertIn("--fields", {
                flag
                for parameter in description["parameters"]
                for flag in parameter.get("flags", [])
            })
            self.assertIn("comparison_token", description["output"]["known_fields"])

    def test_contract_schemas_publish_standard_types_and_result_fields(self) -> None:
        descriptions = describe_commands(
            app,
            (item.command for item in CAPABILITIES if item.availability == "available"),
        )
        for capability in CAPABILITIES:
            if capability.availability != "available":
                continue
            description = descriptions.get(capability.command)
            assert description is not None
            input_schema = description["input"]["schema"]
            input_properties = input_schema["properties"]
            self.assertIsInstance(input_properties, dict, capability.command)
            for name, property_schema in input_properties.items():
                self.assertIn("type", property_schema, f"{capability.command}.{name}")
                if property_schema.get("type") == "array":
                    self.assertIsInstance(property_schema.get("items"), dict, f"{capability.command}.{name}")

            output_schema = description["output"]["schema"]
            data_schema = output_schema["properties"]["data"]
            self.assertEqual(data_schema.get("type"), "object", capability.command)
            if description["output"]["pagination"]:
                result_schema = data_schema["properties"]["items"]["items"]
            else:
                result_schema = data_schema
            self.assertTrue(result_schema.get("properties"), capability.command)
            self.assertEqual(
                sorted(description["output"]["known_fields"]),
                sorted(result_schema["properties"]),
                capability.command,
            )
            receipt_schema = description["output"]["mutation_receipt"]["schema"]
            self.assertIn("request_id", receipt_schema["required"], capability.command)

        professor_update = describe_command(app, "professors.update")
        assert professor_update is not None
        self.assertEqual(
            professor_update["output"]["schema"]["properties"]["data"]["properties"]["recent_papers"]["type"],
            "array",
        )

    def test_paged_collection_contracts_expose_common_pagination_projection_filter_and_export(self) -> None:
        for capability in CAPABILITIES:
            if capability.availability != "available" or not supports_pagination(capability.command):
                continue
            description = describe_command(app, capability.command)
            assert description is not None
            names = {
                flag
                for parameter in description["parameters"]
                if isinstance(parameter, dict)
                for flag in parameter.get("flags", [])
            }
            self.assertTrue(
                {"--cursor", "--limit"}.issubset(names)
                or {"--page", "--page-size"}.issubset(names)
                or {"--offset", "--limit"}.issubset(names),
                capability.command,
            )
            self.assertIn("--fields", names, capability.command)
            self.assertIn("--all", names, capability.command)
            self.assertTrue(capability.to_dict()["supports_file_export"], capability.command)
            self.assertEqual(set(collection_filter_fields(capability.command)), set(capability.to_dict()["filter_fields"]))
            self.assertTrue(capability.to_dict()["filter_fields"], capability.command)

    def test_capabilities_can_filter_by_resource_without_runtime(self) -> None:
        result = CliRunner().invoke(
            app,
            ["--format", "json", "capabilities", "--resource", "communications"],
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        items = payload["data"]["items"]
        self.assertTrue(items)
        self.assertTrue(all(item["resource"] == "communications" for item in items))

    def test_offline_status_and_doctor_contracts_match_their_actual_fields(self) -> None:
        status = describe_command(app, "status")
        doctor = describe_command(app, "doctor")
        assert status is not None
        assert doctor is not None
        self.assertTrue(
            {
                "state",
                "desktop_process_running",
                "backend_ready",
                "protocol_compatible",
            }.issubset(status["output"]["known_fields"]),
        )
        self.assertEqual(
            set(doctor["output"]["known_fields"]),
            {"healthy", "checks", "recommended_action", "repair_command"},
        )
        status_properties = status["output"]["schema"]["properties"]["data"]["properties"]
        doctor_properties = doctor["output"]["schema"]["properties"]["data"]["properties"]
        self.assertEqual(status_properties["desktop_process_running"]["type"], "boolean")
        self.assertEqual(status_properties["backend_ready"]["type"], "boolean")
        self.assertEqual(status_properties["protocol_compatible"]["type"], "boolean")
        self.assertEqual(doctor_properties["healthy"]["type"], "boolean")
        self.assertEqual(doctor_properties["checks"]["type"], "array")
        wait = describe_command(app, "wait")
        assert wait is not None
        self.assertIn("elapsed_seconds", wait["output"]["known_fields"])
        self.assertIn("state_category", wait["output"]["known_fields"])
        self.assertIn("settled", wait["output"]["known_fields"])
        self.assertEqual(
            wait["output"]["schema"]["properties"]["data"]["properties"]["elapsed_seconds"]["type"],
            ["number", "null"],
        )

    def test_if_revision_is_rejected_before_read_requests(self) -> None:
        class _ReadClient(_FakeClient):
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
                self.calls.append(kwargs)
                return super().request(method, path, **kwargs)

        client = _ReadClient()
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=client,
        ):
            result = CliRunner().invoke(
                app,
                [
                    "--format",
                    "json",
                    "--if-revision",
                    "revision-from-read",
                    "professors",
                    "list",
                ],
            )
        self.assertEqual(result.exit_code, 2, msg=result.output)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "IF_REVISION_REQUIRES_WRITE")
        self.assertEqual(client.calls, [])

    def test_if_revision_is_rejected_for_writes_without_backend_revision_support(self) -> None:
        self.assertTrue(supports_if_revision("professors.update"))
        self.assertFalse(supports_if_revision("professors.create"))
        self.assertFalse(supports_if_revision("materials.upload"))
        create = describe_command(app, "professors.create")
        update = describe_command(app, "professors.update")
        assert create is not None
        assert update is not None
        self.assertFalse(create["input"]["global_options"]["if_revision"]["supported"])
        self.assertTrue(update["input"]["global_options"]["if_revision"]["supported"])

        result = CliRunner().invoke(
            app,
            [
                "--format",
                "json",
                "--if-revision",
                "revision-from-read",
                "professors",
                "create",
                "--name",
                "A",
                "--email",
                "a@example.edu",
            ],
        )
        self.assertEqual(result.exit_code, 2, msg=result.output)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "IF_REVISION_REQUIRES_WRITE")

    def test_next_actions_only_reference_real_commands_or_generic_wait(self) -> None:
        registered = {item.command for item in CAPABILITIES}
        descriptions = describe_commands(
            app,
            (item.command for item in CAPABILITIES if item.availability == "available"),
        )
        for capability in CAPABILITIES:
            if capability.availability != "available":
                continue
            description = descriptions.get(capability.command)
            assert description is not None
            for action in description["next_actions"]:
                self.assertTrue(
                    action["command"] in registered or action["command"] == "wait",
                    f"{capability.command} -> {action}",
                )

    def test_wait_is_only_advertised_for_resources_with_a_background_job_route(self) -> None:
        draft = describe_command(app, "drafts.generate")
        matching = describe_command(app, "matching.jobs.create")
        assert draft is not None
        assert matching is not None
        self.assertNotIn("wait", [item["command"] for item in draft["next_actions"]])
        self.assertIn("wait", [item["command"] for item in matching["next_actions"]])

    def test_wait_metadata_is_limited_to_background_lifecycle_commands(self) -> None:
        self.assertFalse(supports_wait("matching.jobs.list"))
        self.assertFalse(supports_wait("matching.jobs.cancel"))
        self.assertFalse(supports_wait("campaigns.create"))
        self.assertFalse(supports_wait("campaigns.stop"))
        self.assertTrue(supports_wait("matching.jobs.create"))
        self.assertTrue(supports_wait("crawler.jobs.get"))
        for command in ("matching.jobs.list", "campaigns.list", "campaigns.stop"):
            description = describe_command(app, command)
            assert description is not None
            self.assertNotIn("wait", [item["command"] for item in description["next_actions"]], command)

    def test_background_creation_contracts_point_to_real_status_and_item_reads(self) -> None:
        expected = {
            "matching.jobs.create": {"matching.jobs.get", "matching.jobs.items", "wait"},
            "enrichment.jobs.create": {"enrichment.jobs.get", "enrichment.jobs.items", "wait"},
            "crawler.jobs.create": {"crawler.jobs.get", "crawler.jobs.events", "crawler.jobs.pages", "crawler.jobs.candidates", "wait"},
        }
        for command, expected_commands in expected.items():
            description = describe_command(app, command)
            assert description is not None
            self.assertEqual(
                {item["command"] for item in description["next_actions"]},
                expected_commands,
                command,
            )

    def test_required_id_collections_match_agent_api_validation(self) -> None:
        expected = {
            "campaigns.create": "professor_ids",
            "campaigns.prepare-send": "item_ids",
            "crawler.jobs.approve": "candidate_ids",
            "enrichment.jobs.create": "professor_ids",
            "matching.jobs.create": "professor_ids",
            "professors.prepare-bulk-archive": "professor_ids",
            "professors.tags.prepare-bulk": "professor_ids",
        }
        for command, parameter_name in expected.items():
            description = describe_command(app, command)
            assert description is not None
            parameter = next(
                item
                for item in description["parameters"]
                if item["name"] == parameter_name
            )
            self.assertTrue(parameter["required"], command)
            self.assertIn(parameter_name, description["input"]["schema"]["required"], command)

        enrich = describe_command(app, "crawler.jobs.enrich")
        assert enrich is not None
        selection_parameter = next(
            item for item in enrich["parameters"] if item["name"] == "selection_mode"
        )
        self.assertTrue(selection_parameter["required"])
        self.assertNotIn("candidate_ids", enrich["input"]["schema"]["required"])
        self.assertTrue(
            {"selection", "submission", "skips", "observation"}.issubset(
                enrich["output"]["known_fields"],
            ),
        )

    def test_detail_contracts_publish_nested_result_fields(self) -> None:
        expected_fields = {
            "communications.threads.get": {"messages", "messages_next_cursor", "messages_has_more"},
            "professors.tags.usage": {"tag", "professors"},
            "campaigns.get": {"identity", "llm_profile", "template", "reference_material"},
            "crawler.jobs.get": {"page_count", "candidate_count", "total_tokens"},
            "enrichment.jobs.get": {"completed_count", "total_tokens", "duration_seconds"},
        }
        for command, fields in expected_fields.items():
            description = describe_command(app, command)
            assert description is not None
            self.assertTrue(fields.issubset(set(description["output"]["known_fields"])), command)

    def test_contracts_publish_runtime_fields_and_collection_envelope(self) -> None:
        professors = describe_command(app, "professors.list")
        materials = describe_command(app, "materials.list")
        jobs = describe_command(app, "matching.jobs.list")
        dashboard = describe_command(app, "dashboard.overview")
        usage = describe_command(app, "usage.records")
        assert professors is not None
        assert materials is not None
        assert jobs is not None
        assert dashboard is not None
        assert usage is not None
        self.assertTrue(
            {"profile_url", "source_url", "recent_papers", "skip_reason"}.issubset(
                professors["output"]["known_fields"],
            ),
        )
        self.assertIn("extracted_text", materials["output"]["known_fields"])
        self.assertTrue(
            {"available_actions", "blocked_actions", "blocked_reason"}.issubset(
                jobs["output"]["known_fields"],
            ),
        )
        self.assertTrue({"mentor", "email"}.issubset(dashboard["output"]["known_fields"]))
        self.assertTrue(
            {"summary", "pagination", "model_options", "records"}.issubset(
                usage["output"]["envelope_fields"],
            ),
        )
        resend = describe_command(app, "campaigns.resend-context")
        assert resend is not None
        self.assertNotIn("available_actions", resend["output"]["known_fields"])

    def test_offset_pagination_is_normalized_and_fetched_to_completion(self) -> None:
        first = normalize_collection_response(
            {"items": [{"id": 1}], "total": 3, "limit": 2, "offset": 0},
        )
        self.assertEqual(first["next_cursor"], "1")
        self.assertTrue(first["has_more"])
        self.assertEqual(first["pagination_mode"], "offset")

        class OffsetClient:
            descriptor = type("Descriptor", (), {"app_version": "test"})()

            def __init__(self) -> None:
                self.offsets: list[int] = []

            def request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
                offset = int((kwargs.get("params") or {}).get("offset", 0))
                limit = int((kwargs.get("params") or {}).get("limit", 2))
                self.offsets.append(offset)
                remaining = max(0, 3 - offset)
                count = min(limit, remaining)
                return {
                    "items": [{"id": index} for index in range(offset, offset + count)],
                    "total": 3,
                    "limit": limit,
                    "offset": offset,
                }

        client = OffsetClient()
        result = fetch_all_pages(client, "/logs", params={"limit": 2, "offset": 0})
        self.assertEqual([item["id"] for item in result["items"]], [0, 1, 2])
        self.assertEqual(client.offsets, [0, 2])

    def test_fetch_all_pages_preserves_an_explicit_starting_cursor(self) -> None:
        class CursorClient:
            def __init__(self) -> None:
                self.cursors: list[object] = []

            def request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
                params = kwargs.get("params") or {}
                cursor = params.get("cursor")
                self.cursors.append(cursor)
                if str(cursor) == "100":
                    return {
                        "items": [{"id": 100}],
                        "next_cursor": "101",
                        "has_more": True,
                    }
                self.assert_cursor(cursor, "101")
                return {
                    "items": [{"id": 101}],
                    "next_cursor": None,
                    "has_more": False,
                }

            @staticmethod
            def assert_cursor(cursor: object, expected: str) -> None:
                if str(cursor) != expected:
                    raise AssertionError(f"expected cursor {expected}, got {cursor!r}")

        client = CursorClient()
        result = fetch_all_pages(client, "/items", params={"cursor": 100, "limit": 2})
        self.assertEqual([item["id"] for item in result["items"]], [100, 101])
        self.assertEqual(client.cursors, ["100", "101"])

    def test_fetch_all_pages_preserves_collection_summary_envelopes(self) -> None:
        class PageClient:
            def __init__(self) -> None:
                self.pages = 0

            def request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
                self.pages += 1
                page = int((kwargs.get("params") or {}).get("page", 1))
                return {
                    "items": [{"id": page}],
                    "records": [{"id": page}],
                    "summary": {"total_tokens": 9},
                    "model_options": ["model-a"],
                    "pagination": {"page": page, "total_pages": 2},
                    "next_cursor": str(page + 1) if page == 1 else None,
                    "has_more": page == 1,
                    "pagination_mode": "page",
                }

        result = fetch_all_pages(PageClient(), "/usage", params={"page": 1, "page_size": 1})
        self.assertEqual(result["records"], [{"id": 1}, {"id": 2}])
        self.assertEqual(result["summary"], {"total_tokens": 9})
        self.assertEqual(result["model_options"], ["model-a"])

    def test_non_paged_envelopes_with_items_are_not_misclassified_as_collections(self) -> None:
        envelope = {
            "task": {"id": 7},
            "items": [{"id": 11, "status": "review_required"}],
            "summary": {"total": 1},
        }
        normalized = normalize_collection_response(envelope, command="campaigns.resend-context")
        self.assertEqual(normalized, envelope)

    def test_l3_and_high_impact_l2_operations_have_confirmation_plans(self) -> None:
        for capability in CAPABILITIES:
            if capability.availability != "available":
                continue
            if capability.risk_level == "L3":
                self.assertTrue(capability.requires_plan, capability.command)
            high_impact_name = any(
                token in capability.command
                for token in (
                    "bulk",
                    "prepare-delete",
                    "prepare-archive",
                    "prepare-import",
                    "prepare-send",
                    "import",
                )
            )
            if capability.risk_level == "L2" and capability.mutates and high_impact_name:
                self.assertTrue(capability.requires_plan, capability.command)

    def test_high_risk_contracts_disclose_scope_external_cost_and_confirmation(self) -> None:
        for capability in CAPABILITIES:
            if capability.availability != "available" or capability.risk_level not in {"L2", "L3"}:
                continue
            description = describe_command(app, capability.command)
            assert description is not None
            effects = description["effects"]
            self.assertIsInstance(effects, dict)
            assert isinstance(effects, dict)
            self.assertTrue(effects.get("impact_scope"), capability.command)
            self.assertIsInstance(effects.get("external_services"), list, capability.command)
            self.assertIn("cost_may_apply", effects, capability.command)
            self.assertIn("confirmation_rule", effects, capability.command)
            self.assertIn("unknown_external_result_protection", effects, capability.command)
            self.assertEqual(
                effects["requires_confirmation_plan"],
                effects["plan_role"] == "consumer",
            )
            self.assertIn(effects["plan_role"], {"none", "producer", "consumer", "delegated"})
            self.assertIn("current_effects", effects)
            self.assertIn("downstream_effects", effects)

    def test_plan_producers_consumers_and_delegated_invoke_have_distinct_semantics(self) -> None:
        producer = describe_command(app, "drafts.prepare-send")
        consumer = describe_command(app, "plans.execute")
        delegated = describe_command(app, "invoke")
        assert producer is not None
        assert consumer is not None
        assert delegated is not None

        self.assertEqual(producer["effects"]["plan_role"], "producer")
        self.assertTrue(producer["effects"]["produces_confirmation_plan"])
        self.assertFalse(producer["effects"]["confirmation_required_before_invocation"])
        self.assertEqual(producer["effects"]["current_effects"]["external_services"], [])
        self.assertEqual(producer["effects"]["downstream_effects"]["external_services"], ["smtp"])
        compact_producer = compact_command_description(producer)
        self.assertIn("downstream_mutates", compact_producer["risk"]["traits"])
        self.assertIn("mutates", compact_producer["effects"]["downstream"]["traits"])

        self.assertEqual(consumer["effects"]["plan_role"], "consumer")
        self.assertFalse(consumer["effects"]["produces_confirmation_plan"])
        self.assertTrue(consumer["effects"]["confirmation_required_before_invocation"])
        self.assertTrue(consumer["effects"]["delegated_effects"])
        self.assertTrue(consumer["effects"]["requires_target_contract"])
        self.assertTrue(consumer["effects"]["downstream_effects"]["mutates"])

        self.assertEqual(delegated["risk"]["risk_mode"], "delegated")
        self.assertEqual(delegated["effects"]["risk_mode"], "delegated")
        self.assertEqual(delegated["effects"]["plan_role"], "delegated")
        self.assertFalse(delegated["effects"]["mutates"])
        self.assertEqual(delegated["effects"]["external_services"], [])
        self.assertTrue(delegated["effects"]["delegated_effects"])
        self.assertTrue(delegated["effects"]["requires_target_contract"])
        self.assertTrue(delegated["risk"]["delegated_effects"])
        self.assertTrue(delegated["risk"]["requires_target_contract"])

        compact_delegated = compact_command_description(delegated)
        self.assertIn("delegated_effects", compact_delegated["risk"]["traits"])
        self.assertIn("requires_target_contract", compact_delegated["risk"]["traits"])

    def test_compact_description_does_not_mutate_full_next_actions(self) -> None:
        description = describe_command(app, "plans.execute")
        self.assertIsNotNone(description)
        assert description is not None
        original_actions = deepcopy(description["next_actions"])

        # Force every compacting stage so the regression is independent of the
        # command's current prose length and global byte budget.
        description["summary"] = "x" * 10_000
        compact = compact_command_description(description)

        self.assertEqual(description["next_actions"], original_actions)
        self.assertNotIn("next_actions", compact)

    def test_offline_and_wait_error_lifecycle_contracts_are_precise(self) -> None:
        version = describe_command(app, "version")
        wait = describe_command(app, "wait")
        assert version is not None
        assert wait is not None
        self.assertEqual([item["code"] for item in version["errors"]], ["INVALID_ARGUMENT"])
        self.assertTrue(wait["output"]["terminal_states"])
        self.assertTrue(wait["state_transitions"])

    def test_plan_previews_do_not_claim_an_llm_call(self) -> None:
        for command in (
            "campaigns.prepare-send",
            "campaigns.prepare-resume",
            "campaigns.prepare-restore-item-send",
            "test-email.prepare-send",
        ):
            description = describe_command(app, command)
            assert description is not None
            self.assertNotIn("llm", description["effects"]["external_services"], command)
            self.assertFalse(description["effects"]["cost_may_apply"], command)
        models = describe_command(app, "llm-profiles.models")
        assert models is not None
        self.assertIn("llm", models["effects"]["external_services"])
        self.assertFalse(models["effects"]["cost_may_apply"])

    def test_available_describe_contracts_have_no_secret_input_names(self) -> None:
        secret_parts = ("password", "api_key", "secret", "credential", "access_token")
        descriptions = describe_commands(
            app,
            (item.command for item in CAPABILITIES if item.availability == "available"),
        )
        for capability in CAPABILITIES:
            if capability.availability != "available":
                continue
            description = descriptions.get(capability.command)
            assert description is not None
            for parameter in description["parameters"]:
                name = str(parameter["name"]).lower()
                self.assertFalse(any(part in name for part in secret_parts), capability.command)
        redacted = _redact_receipt_value(
            {
                "api_key": "known-api-secret",
                "nested": {
                    "password": "known-password-secret",
                    "access_token": "known-token-secret",
                    "items": [{"client_secret": "known-client-secret", "name": "safe"}],
                },
            },
        )
        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["password"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["access_token"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["items"][0]["client_secret"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["items"][0]["name"], "safe")
        self.assertNotIn(
            "known-api-secret",
            json.dumps(redacted, ensure_ascii=False),
        )

    def test_collection_fields_and_jsonl_export_keep_stdout_small(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "professors.jsonl"
            with patch(
                "auto_email_sender_cli.commands.common.AgentApiClient",
                return_value=_FakeClient(),
            ):
                result = CliRunner().invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "--output-file",
                        destination.as_posix(),
                        "professors",
                        "list",
                        "--fields",
                        "id,name",
                    ],
                )
            self.assertEqual(result.exit_code, 0, msg=result.output)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["data"]["item_count"], 2)
            self.assertEqual(payload["data"]["selected_fields"], ["id", "name"])
            lines = destination.read_text(encoding="utf-8").splitlines()
            first = json.loads(lines[0])
            self.assertEqual(first["id"], 1)
            self.assertEqual(first["name"], "A")
            self.assertRegex(first["revision"], r"^[0-9a-f]{20}$")

    def test_all_output_file_streams_multiple_pages_and_preserves_summary(self) -> None:
        class StreamingClient:
            descriptor = type("Descriptor", (), {"app_version": "test"})()
            last_request_id = "request-stream"

            def __init__(self) -> None:
                self.cursors: list[object] = []

            def request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
                params = kwargs.get("params") or {}
                cursor = params.get("cursor")
                self.cursors.append(cursor)
                if cursor is None:
                    return {
                        "items": [
                            {"id": 1, "name": "A", "email": "a@example.edu"},
                            {"id": 2, "name": "B", "email": "b@example.edu"},
                        ],
                        "next_cursor": "2",
                        "has_more": True,
                        "total": 3,
                        "summary": {"record_count": 3},
                    }
                return {
                    "items": [{"id": 3, "name": "C", "email": "c@example.edu"}],
                    "next_cursor": None,
                    "has_more": False,
                    "total": 3,
                }

        client = StreamingClient()
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "all-professors.jsonl"
            with patch(
                "auto_email_sender_cli.commands.common.AgentApiClient",
                return_value=client,
            ):
                result = CliRunner().invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "--output-file",
                        destination.as_posix(),
                        "professors",
                        "list",
                        "--all",
                        "--fields",
                        "id,name",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            payload = json.loads(result.stdout)["data"]
            self.assertEqual(payload["item_count"], 3)
            self.assertEqual(payload["page_count"], 2)
            self.assertEqual(payload["source_total"], 3)
            self.assertEqual(payload["summary"], {"record_count": 3})
            self.assertEqual(payload["selected_fields"], ["id", "name"])
            self.assertNotIn("items", payload)
            lines = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["id"] for item in lines], [1, 2, 3])
            self.assertTrue(all("email" not in item for item in lines))
            self.assertEqual(client.cursors, [None, "2"])

    def test_all_output_file_applies_filter_across_streamed_pages(self) -> None:
        class StreamingClient:
            descriptor = type("Descriptor", (), {"app_version": "test"})()
            last_request_id = "request-filter-stream"

            def request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
                params = kwargs.get("params") or {}
                if params.get("cursor") is None:
                    return {
                        "items": [
                            {"id": 1, "name": "Keep Alpha"},
                            {"id": 2, "name": "Drop Beta"},
                        ],
                        "next_cursor": "2",
                        "has_more": True,
                        "total": 4,
                        "summary": {"record_count": 4},
                    }
                return {
                    "items": [
                        {"id": 3, "name": "Drop Gamma"},
                        {"id": 4, "name": "Keep Delta"},
                    ],
                    "next_cursor": None,
                    "has_more": False,
                    "total": 4,
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "filtered-professors.jsonl"
            with patch(
                "auto_email_sender_cli.commands.common.AgentApiClient",
                return_value=StreamingClient(),
            ):
                result = CliRunner().invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "--filter",
                        '{"name":{"contains":"keep"}}',
                        "--output-file",
                        destination.as_posix(),
                        "professors",
                        "list",
                        "--all",
                    ],
                )

            self.assertEqual(result.exit_code, 0, msg=result.output)
            payload = json.loads(result.stdout)["data"]
            self.assertEqual(payload["item_count"], 2)
            self.assertEqual(payload["source_total"], 4)
            self.assertEqual(payload["page_count"], 2)
            self.assertTrue(payload["filter_applied"])
            self.assertEqual(payload["summary"], {"record_count": 2})
            lines = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["id"] for item in lines], [1, 4])

    def test_streaming_export_does_not_overwrite_an_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "existing.jsonl"
            destination.write_text("keep me\n", encoding="utf-8")
            with patch(
                "auto_email_sender_cli.commands.common.AgentApiClient",
                return_value=_FakeClient(),
            ):
                result = CliRunner().invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "--output-file",
                        destination.as_posix(),
                        "professors",
                        "list",
                        "--all",
                    ],
                )
            self.assertEqual(result.exit_code, 2, msg=result.output)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "OUTPUT_EXISTS")
            self.assertEqual(destination.read_text(encoding="utf-8"), "keep me\n")

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not portable on Windows")
    def test_collection_exports_are_private_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "professors.jsonl"
            with patch(
                "auto_email_sender_cli.commands.common.AgentApiClient",
                return_value=_FakeClient(),
            ):
                result = CliRunner().invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "--output-file",
                        destination.as_posix(),
                        "professors",
                        "list",
                        "--all",
                    ],
                )
            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_streaming_export_falls_back_when_hard_links_are_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "professors.jsonl"
            with (
                patch(
                    "auto_email_sender_cli.commands.common.AgentApiClient",
                    return_value=_FakeClient(),
                ),
                patch(
                    "auto_email_sender_cli.commands.common.os.link",
                    side_effect=OSError("hard links unsupported"),
                ),
            ):
                result = CliRunner().invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "--output-file",
                        destination.as_posix(),
                        "professors",
                        "list",
                        "--all",
                    ],
                )
            self.assertEqual(result.exit_code, 0, msg=result.output)
            rows = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["id"] for row in rows], [1, 2])

    def test_streaming_export_fallback_never_overwrites_an_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "professors.jsonl"
            destination.write_text("keep me\n", encoding="utf-8")
            with (
                patch(
                    "auto_email_sender_cli.commands.common.AgentApiClient",
                    return_value=_FakeClient(),
                ),
                patch(
                    "auto_email_sender_cli.commands.common.os.link",
                    side_effect=OSError("hard links unsupported"),
                ),
            ):
                result = CliRunner().invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "--output-file",
                        destination.as_posix(),
                        "professors",
                        "list",
                        "--all",
                    ],
                )
            self.assertEqual(result.exit_code, 2, msg=result.output)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "OUTPUT_EXISTS")
            self.assertEqual(destination.read_text(encoding="utf-8"), "keep me\n")

    def test_all_stdout_limit_fails_with_an_output_file_recovery_action(self) -> None:
        with self.assertRaises(CliError) as raised:
            fetch_all_pages(_FakeClient(), "/api/agent/v1/professors", max_items=1)
        self.assertEqual(raised.exception.code, "RESULT_TOO_LARGE")
        self.assertIn("--output-file", raised.exception.suggested_command or "")

    def test_field_projection_preserves_full_record_revision_and_rejects_unknown_empty_field(self) -> None:
        full_record = {"id": 1, "name": "A", "email": "a@example.edu"}
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=_FakeClient(),
        ):
            projected = CliRunner().invoke(
                app,
                [
                    "--format",
                    "json",
                    "professors",
                    "list",
                    "--fields",
                    "id,name",
                ],
            )
        self.assertEqual(projected.exit_code, 0, msg=projected.output)
        projected_item = json.loads(projected.stdout)["data"]["items"][0]
        self.assertEqual(projected_item["revision"], _with_revision(full_record)["revision"])
        self.assertEqual(projected_item["name"], "A")
        self.assertNotIn("email", projected_item)

        class _EmptyClient(_FakeClient):
            def request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
                return {"items": [], "next_cursor": None, "has_more": False}

        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=_EmptyClient(),
        ):
            invalid = CliRunner().invoke(
                app,
                [
                    "--format",
                    "json",
                    "professors",
                    "list",
                    "--fields",
                    "not_a_declared_field",
                ],
            )
        self.assertNotEqual(invalid.exit_code, 0)
        self.assertEqual(json.loads(invalid.stdout)["error"]["code"], "INVALID_FIELD_SELECTION")

        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=_EmptyClient(),
        ):
            revision_projection = CliRunner().invoke(
                app,
                [
                    "--format",
                    "json",
                    "professors",
                    "list",
                    "--fields",
                    "revision",
                ],
            )
        self.assertEqual(revision_projection.exit_code, 0, msg=revision_projection.output)
        self.assertEqual(json.loads(revision_projection.stdout)["data"]["items"], [])

    def test_structured_filter_is_whitelisted_and_fetches_complete_collection(self) -> None:
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=_FakeClient(),
        ):
            result = CliRunner().invoke(
                app,
                [
                    "--format",
                    "json",
                    "--filter",
                    '{"name":{"contains":"b"}}',
                    "professors",
                    "list",
                ],
            )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        payload = json.loads(result.stdout)
        self.assertEqual([item["name"] for item in payload["data"]["items"]], ["B"])
        self.assertTrue(payload["data"]["fetched_all"])

        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=_FakeClient(),
        ):
            invalid = CliRunner().invoke(
                app,
                [
                    "--format",
                    "json",
                    "--filter",
                    '{"name":{"sql":"x"}}',
                    "professors",
                    "list",
                ],
            )
        self.assertNotEqual(invalid.exit_code, 0)
        self.assertEqual(json.loads(invalid.stdout)["error"]["code"], "INVALID_FILTER")

        invalid_type = CliRunner().invoke(
            app,
            [
                "--format",
                "json",
                "--filter",
                '{"name":{"in":"A"}}',
                "professors",
                "list",
            ],
        )
        self.assertEqual(invalid_type.exit_code, 2, msg=invalid_type.output)
        self.assertEqual(json.loads(invalid_type.stdout)["error"]["code"], "INVALID_FILTER")

    def test_native_filter_pushdown_reduces_backend_work_with_local_fallback(self) -> None:
        self.assertEqual(
            server_filter_params(
                '{"identity_id":{"eq":7},"has_reply":true,"professor_name":{"contains":"Li"}}',
                command="communications.threads.list",
            ),
            {"identity_id": 7, "replied": True},
        )
        self.assertEqual(
            server_filter_params(
                '{"identity_id":{"ne":7}}',
                command="communications.threads.list",
            ),
            {},
        )
        self.assertEqual(
            server_filter_params(
                '{"identity_id":{"eq":"not-an-id"},"direction":"invalid"}',
                command="communications.messages.list",
            ),
            {},
        )

        class RecordingClient:
            descriptor = type("Descriptor", (), {"app_version": "test"})()
            last_request_id = "request-filter-pushdown"
            calls: list[dict[str, object]] = []

            def request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
                self.calls.append(dict(kwargs.get("params", {})))
                # Simulate an older backend that ignores the pushed parameter;
                # the CLI's local pass must still remove the non-matching row.
                return {
                    "items": [
                        {"id": "7:1", "identity_id": 7, "has_reply": True},
                        {"id": "8:2", "identity_id": 8, "has_reply": False},
                    ],
                    "next_cursor": None,
                    "has_more": False,
                }

        client = RecordingClient()
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=client,
        ):
            result = CliRunner().invoke(
                app,
                [
                    "--format",
                    "json",
                    "--filter",
                    '{"identity_id":{"eq":7}}',
                    "communications",
                    "threads",
                    "list",
                ],
            )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(client.calls[0]["identity_id"], 7)
        items = json.loads(result.stdout)["data"]["items"]
        self.assertEqual([item["identity_id"] for item in items], [7])

    def test_revision_precedes_derived_action_metadata(self) -> None:
        record = {"id": 9, "status": "partially_completed", "name": "Campaign"}
        expected_revision = _with_revision(record)["revision"]

        class StateClient:
            descriptor = type("Descriptor", (), {"app_version": "test"})()
            last_request_id = "request-state-revision"

            def request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
                return {"items": [record], "next_cursor": None, "has_more": False}

        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=StateClient(),
        ):
            result = CliRunner().invoke(
                app,
                ["--format", "json", "campaigns", "list"],
            )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        item = json.loads(result.stdout)["data"]["items"][0]
        self.assertEqual(item["revision"], expected_revision)
        self.assertTrue(item["available_actions"])
        derived_revision = _with_revision(
            {key: value for key, value in item.items() if key != "revision"},
        )["revision"]
        self.assertNotEqual(derived_revision, expected_revision)

    def test_usage_filter_recomputes_token_summary_for_filtered_records(self) -> None:
        data = {
            "items": [
                {"id": 1, "feature_type": "crawl", "input_tokens": 4, "output_tokens": 2, "cached_tokens": 1, "total_tokens": 6},
                {"id": 2, "feature_type": "draft_generation", "input_tokens": 10, "output_tokens": 3, "cached_tokens": 0, "total_tokens": 13},
            ],
            "summary": {"input_tokens": 14, "output_tokens": 5, "cached_tokens": 1, "total_tokens": 19, "record_count": 2},
            "next_cursor": None,
            "has_more": False,
        }
        filtered = apply_structured_filter(
            data,
            '{"feature_type":{"eq":"crawl"}}',
            command="usage.records",
        )
        self.assertEqual(filtered["summary"], {"input_tokens": 4, "output_tokens": 2, "cached_tokens": 1, "total_tokens": 6, "record_count": 1})

    def test_filter_on_detail_command_fails_before_issuing_a_request(self) -> None:
        class _DetailClient(_FakeClient):
            def __init__(self) -> None:
                self.descriptor = type("Descriptor", (), {"app_version": "test"})()
                self.last_request_id = None
                self.last_response_headers = {}
                self.calls: list[dict[str, object]] = []

            def request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
                self.calls.append(kwargs)
                return {"id": 1, "name": "A", "email": "a@example.edu"}

        client = _DetailClient()
        with patch(
            "auto_email_sender_cli.commands.common.AgentApiClient",
            return_value=client,
        ):
            result = CliRunner().invoke(
                app,
                [
                    "--format",
                    "json",
                    "--filter",
                    '{"name":{"contains":"A"}}',
                    "professors",
                    "get",
                    "1",
                ],
            )
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "FILTER_NOT_SUPPORTED")
        self.assertEqual(client.calls, [])

    def test_collection_only_global_options_are_not_silently_ignored_by_writes(self) -> None:
        result = CliRunner().invoke(
            app,
            [
                "--format",
                "json",
                "--filter",
                '{"name":{"contains":"A"}}',
                "professors",
                "create",
                "--name",
                "A",
                "--email",
                "a@example.edu",
            ],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "FILTER_NOT_SUPPORTED")

    def test_mutation_receipt_finds_nonstandard_affected_ids(self) -> None:
        receipt = add_mutation_receipt(
            {"ok": True, "group_id": 17},
            command="communication-groups.delete",
            request_id="req-group-delete",
            json_body={"group_id": 17},
        )["mutation_receipt"]
        self.assertEqual(receipt["changed_resources"][0]["id"], "17")

    def test_direct_file_handlers_reject_collection_only_global_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "professors.csv"
            with patch(
                "auto_email_sender_cli.commands.professors.AgentApiClient",
                return_value=_FakeClient(),
            ):
                filtered = CliRunner().invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "--filter",
                        '{"name":{"contains":"A"}}',
                        "professors",
                        "export",
                        "--output",
                        export_path.as_posix(),
                    ],
                )
            self.assertEqual(filtered.exit_code, 2, msg=filtered.output)
            self.assertEqual(json.loads(filtered.stdout)["error"]["code"], "FILTER_NOT_SUPPORTED")
            self.assertFalse(export_path.exists())

            download_path = Path(temp_dir) / "material.pdf"
            with patch(
                "auto_email_sender_cli.commands.resources.AgentApiClient",
                return_value=_FakeClient(),
            ):
                redirected = CliRunner().invoke(
                    app,
                    [
                        "--format",
                        "json",
                        "--output-file",
                        (Path(temp_dir) / "items.jsonl").as_posix(),
                        "materials",
                        "download",
                        "8",
                        "--output",
                        download_path.as_posix(),
                    ],
                )
            self.assertEqual(redirected.exit_code, 2, msg=redirected.output)
            self.assertEqual(
                json.loads(redirected.stdout)["error"]["code"],
                "OUTPUT_FILE_REQUIRES_COLLECTION",
            )
            self.assertFalse(download_path.exists())

    def test_state_metadata_covers_non_terminal_partial_and_nested_task_states(self) -> None:
        data = {
            "id": 42,
            "status": "partially_completed",
            "current_task": {
                "task_id": 8,
                "status": "review_required",
                "approved_body_text": "正文",
            },
        }
        projected = augment_state_metadata(data, command="campaigns.get")
        action_map = {
            item["action"]: item
            for item in projected["available_actions"]
        }
        self.assertEqual(projected["status"], "partially_completed")
        self.assertEqual(action_map["read"]["command"], "campaigns.get")
        self.assertEqual(action_map["read"]["arguments"], {"campaign_id": 42})
        self.assertIn("retry", projected["blocked_actions"])
        self.assertIn("wait", projected["blocked_actions"])
        task_actions = {
            item["action"]: item
            for item in projected["current_task"]["available_actions"]
        }
        self.assertEqual(task_actions["prepare-send"]["command"], "drafts.prepare-send")
        self.assertEqual(task_actions["prepare-send"]["arguments"], {"task_id": 8})

        campaign_item = augment_state_metadata(
            {"id": 99, "campaign_id": 42, "status": "review_required"},
            command="campaigns.items",
        )
        item_actions = {
            item["action"]: item
            for item in campaign_item["available_actions"]
        }
        prepare_send = item_actions["prepare-send"]
        self.assertEqual(prepare_send["command"], "campaigns.prepare-send")
        self.assertEqual(prepare_send["arguments"], {"campaign_id": 42, "item_ids": [99]})
        self.assertEqual(prepare_send["risk_level"], "L3")
        self.assertNotIn("confirmation_required", prepare_send)
        self.assertNotIn("confirmation_required_before_invocation", prepare_send)
        self.assertTrue(prepare_send["produces_confirmation_plan"])
        self.assertEqual(prepare_send["plan_role"], "producer")
        self.assertNotIn("blocked_reason", prepare_send)

        plan = augment_state_metadata(
            {"plan_id": "plan-7", "status": "pending"},
            command="plans.show",
        )
        plan_actions = {item["action"]: item for item in plan["available_actions"]}
        self.assertEqual(plan_actions["execute"]["command"], "plans.execute")
        self.assertEqual(plan_actions["execute"]["arguments"], {"plan_id": "plan-7"})
        self.assertTrue(
            plan_actions["execute"]["confirmation_required_before_invocation"],
        )
        self.assertNotIn("confirmation_required", plan_actions["execute"])
        self.assertNotIn("produces_confirmation_plan", plan_actions["execute"])
        self.assertEqual(plan_actions["execute"]["plan_role"], "consumer")
        self.assertEqual(plan_actions["execute"]["required_input"], ["confirm"])

    def test_read_only_status_fields_do_not_gain_lifecycle_actions(self) -> None:
        projected = augment_state_metadata(
            {"status": "success", "total_tokens": 3},
            command="usage.records",
        )
        self.assertNotIn("available_actions", projected)
        self.assertNotIn("blocked_actions", projected)

    def test_every_stateful_contract_and_known_state_has_explicit_actions(self) -> None:
        known_states = {
            "queued",
            "running",
            "paused",
            "needs_review",
            "completed",
            "partially_completed",
            "partial_failed",
            "failed",
            "canceled",
        }
        for capability in CAPABILITIES:
            if capability.availability != "available" or not capability_stateful(capability.command):
                continue
            description = describe_command(app, capability.command)
            assert description is not None
            output = description["output"]
            self.assertIsInstance(output, dict)
            assert isinstance(output, dict)
            self.assertIsInstance(output.get("state_metadata"), dict, capability.command)
            self.assertTrue(output.get("terminal_states"), capability.command)
            self.assertTrue(description["state_transitions"], capability.command)
            for status in known_states:
                projected = augment_state_metadata({"status": status}, command=capability.command)
                self.assertIsInstance(projected.get("available_actions"), list, capability.command)
                self.assertIsInstance(projected.get("blocked_actions"), dict, capability.command)
                if status in {"queued", "running"}:
                    self.assertNotIn("succeeded", projected["available_actions"])

    def test_wait_state_rules_do_not_confuse_paused_unknown_or_partial_results(self) -> None:
        paused = _available_actions("crawler.jobs", 7, "paused")
        paused_actions = {item["action"]: item for item in paused}
        self.assertEqual(paused_actions["resume"]["command"], "crawler.jobs.resume")
        self.assertNotIn("wait", paused_actions)

        unknown = _available_actions("matching.jobs", 8, "mystery")
        self.assertEqual([item["action"] for item in unknown], ["read"])
        self.assertEqual(unknown[0]["command"], "matching.jobs.get")

        partial = _available_actions("enrichment.jobs", 9, "partially_completed")
        partial_actions = {item["action"]: item for item in partial}
        self.assertEqual(partial_actions["retry"]["command"], "enrichment.jobs.retry-failed")
        self.assertNotIn("wait", partial_actions)


if __name__ == "__main__":
    unittest.main()
