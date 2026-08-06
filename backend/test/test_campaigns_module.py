from __future__ import annotations

import ast
import importlib
from pathlib import Path
import subprocess
import sys
import unittest


LEGACY_MODULE_OWNERS = {
    "app.api.batch_tasks": "app.modules.campaigns.batch_tasks.api",
    "app.api.outreach_templates": "app.modules.campaigns.templates.api",
    "app.schemas.batch_task": "app.modules.campaigns.schemas",
    "app.schemas.outreach_template": "app.modules.campaigns.templates.schemas",
    "app.services.agent_campaigns": "app.modules.campaigns.agent",
    "app.services.batch_schedule": "app.modules.campaigns.scheduling",
    "app.services.batch_task_status": "app.modules.campaigns.status",
    "app.services.batch_task_item_actions": "app.modules.campaigns.item_actions",
    "app.services.batch_task_resend_context": "app.modules.campaigns.resend",
    "app.services.batch_draft_fallback": "app.modules.campaigns.drafts.fallback",
    "app.services.batch_draft_generation_runtime": "app.modules.campaigns.drafts.runtime",
    "app.services.outreach_template_library": "app.modules.campaigns.templates.library",
    "app.services.outreach_template_mutations": "app.modules.campaigns.templates.mutations",
    "app.services.outreach_templates": "app.modules.campaigns.templates.rendering",
}


def _owned_public_names(module: object) -> set[str]:
    module_path = Path(str(getattr(module, "__file__")))
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.add(target.id)
    return names


class CampaignsModuleCompatibilityTest(unittest.TestCase):
    def test_all_legacy_exports_reference_campaign_module_owners(self) -> None:
        for legacy_name, owner_name in LEGACY_MODULE_OWNERS.items():
            with self.subTest(legacy=legacy_name):
                legacy = importlib.import_module(legacy_name)
                owner = importlib.import_module(owner_name)
                public_names = _owned_public_names(owner)
                self.assertTrue(public_names, owner_name)
                for name in public_names:
                    self.assertIs(
                        getattr(legacy, name),
                        getattr(owner, name),
                        msg=f"{legacy_name}.{name} must reference {owner_name}.{name}",
                    )

    def test_public_facade_reexports_cross_domain_contracts(self) -> None:
        from app.modules.campaigns import agent, item_actions, public, schemas, status
        from app.modules.campaigns.templates import library, rendering

        self.assertIs(public.BatchTaskCardRead, schemas.BatchTaskCardRead)
        self.assertIs(
            public.batch_item_uses_llm_generation,
            item_actions.batch_item_uses_llm_generation,
        )
        self.assertIs(public.sync_batch_task_completion, status.sync_batch_task_completion)
        self.assertIs(
            public.get_default_outreach_template_for_identity,
            library.get_default_outreach_template_for_identity,
        )
        self.assertIs(public.build_template_context, rendering.build_template_context)
        self.assertIs(public.get_agent_campaign, agent.get_agent_campaign)

    def test_schema_aggregate_references_campaign_owner(self) -> None:
        from app import schemas as aggregate
        from app.modules.campaigns import schemas

        self.assertIs(aggregate.BatchTaskCardRead, schemas.BatchTaskCardRead)
        self.assertIs(aggregate.CreateBatchTaskRequest, schemas.CreateBatchTaskRequest)

    def test_legacy_modules_import_independently(self) -> None:
        for module_name in LEGACY_MODULE_OWNERS:
            with self.subTest(module=module_name):
                completed = subprocess.run(
                    [sys.executable, "-c", f"import {module_name}"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
