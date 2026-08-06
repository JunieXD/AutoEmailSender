from __future__ import annotations

import subprocess
import sys
import unittest


class WorkspaceModuleCompatibilityTest(unittest.TestCase):
    def test_legacy_http_exports_reference_workspace_owners(self) -> None:
        from app.api import email_tasks as legacy_tasks
        from app.api import workspaces as legacy_workspaces
        from app.modules.workspace import api as workspace_api
        from app.modules.workspace.tasks import api as task_api

        self.assertIs(legacy_workspaces.router, workspace_api.router)
        self.assertIs(
            legacy_workspaces.refresh_workspace_replies,
            workspace_api.refresh_workspace_replies,
        )
        self.assertIs(legacy_tasks.router, task_api.router)
        self.assertIs(legacy_tasks.save_draft, task_api.save_draft)

    def test_legacy_schema_exports_reference_workspace_owners(self) -> None:
        from app.modules.workspace import schemas as workspace_schemas
        from app.modules.workspace.tasks import schemas as task_schemas
        from app.schemas import email_task as legacy_tasks
        from app.schemas import workspace as legacy_workspace

        self.assertIs(
            legacy_workspace.WorkspaceThreadRead,
            workspace_schemas.WorkspaceThreadRead,
        )
        self.assertIs(
            legacy_tasks.EmailTaskApprovalRequest,
            task_schemas.EmailTaskApprovalRequest,
        )

    def test_legacy_thread_exports_reference_workspace_owner(self) -> None:
        from app.api import workspace_support as legacy
        from app.modules.workspace import thread

        self.assertIs(legacy.build_workspace_thread, thread.build_workspace_thread)
        self.assertIs(legacy.ensure_workspace_task, thread.ensure_workspace_task)

    def test_legacy_runtime_exports_reference_task_owners(self) -> None:
        from app.modules.workspace.tasks import delivery, runtime
        from app.services import task_runtime as legacy

        self.assertIs(legacy.generate_task_draft, runtime.generate_task_draft)
        self.assertIs(legacy.approve_draft_task, runtime.approve_draft_task)
        self.assertIs(legacy.dispatch_due_tasks_once, delivery.dispatch_due_tasks_once)
        self.assertIs(legacy.dispatch_email_task, delivery.dispatch_email_task)

    def test_public_facade_reexports_workspace_contracts(self) -> None:
        from app.modules.workspace import public, schemas, thread
        from app.modules.workspace.tasks import (
            delivery,
            runtime,
            schemas as task_schemas,
        )

        self.assertIs(public.WorkspaceThreadRead, schemas.WorkspaceThreadRead)
        self.assertIs(
            public.EmailTaskApprovalRequest,
            task_schemas.EmailTaskApprovalRequest,
        )
        self.assertIs(public.build_workspace_thread, thread.build_workspace_thread)
        self.assertIs(public.generate_task_draft, runtime.generate_task_draft)
        self.assertIs(public.dispatch_email_task, delivery.dispatch_email_task)

    def test_schema_aggregate_references_workspace_owners(self) -> None:
        from app import schemas as aggregate
        from app.modules.workspace import schemas
        from app.modules.workspace.tasks import schemas as task_schemas

        self.assertIs(aggregate.WorkspaceThreadRead, schemas.WorkspaceThreadRead)
        self.assertIs(
            aggregate.EmailTaskApprovalRequest,
            task_schemas.EmailTaskApprovalRequest,
        )

    def test_legacy_modules_import_independently(self) -> None:
        module_names = (
            "app.api.email_tasks",
            "app.api.workspaces",
            "app.api.workspace_support",
            "app.schemas.email_task",
            "app.schemas.workspace",
            "app.services.task_runtime",
        )
        for module_name in module_names:
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
