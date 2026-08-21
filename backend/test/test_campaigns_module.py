from __future__ import annotations

import unittest


class CampaignsModuleBoundaryTest(unittest.TestCase):
    def test_public_facade_reexports_cross_domain_contracts(self) -> None:
        from app.modules.campaigns import agent, item_actions, public, schemas, status
        from app.modules.campaigns.templates import library, rendering

        self.assertIs(public.BatchTaskCardRead, schemas.BatchTaskCardRead)
        self.assertIs(
            public.batch_item_uses_llm_generation,
            item_actions.batch_item_uses_llm_generation,
        )
        self.assertIs(
            public.sync_batch_task_completion, status.sync_batch_task_completion
        )
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


if __name__ == "__main__":
    unittest.main()
