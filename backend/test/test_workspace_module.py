from __future__ import annotations

import unittest


class WorkspaceModuleBoundaryTest(unittest.TestCase):
    def test_public_facade_reexports_workspace_contracts(self) -> None:
        from app.modules.workspace import public, schemas, thread
        from app.modules.workspace.tasks import delivery, runtime
        from app.modules.workspace.tasks import schemas as task_schemas

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


if __name__ == "__main__":
    unittest.main()
