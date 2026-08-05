from __future__ import annotations

import unittest

from app.services.agent_change_plans import (
    CAMPAIGN_CREATE_ACTION,
    CAMPAIGN_RESTORE_SEND_ACTION,
    CAMPAIGN_RESUME_ACTION,
    CAMPAIGN_SEND_ACTION,
    COMMUNITY_MENTOR_IMPORT_ACTION,
    CRAWL_CANDIDATE_APPROVE_ACTION,
    CRAWL_JOB_RETRY_ACTION,
    MATERIAL_DELETE_ACTION,
    PROFESSOR_BULK_ARCHIVE_ACTION,
    PROFESSOR_BULK_TAGS_ACTION,
    PROFESSOR_IMPORT_ACTION,
    PROFESSOR_TAG_DELETE_ACTION,
    TEMPLATE_ARCHIVE_ACTION,
    TEST_EMAIL_SEND_ACTION,
)
from app.services.agent_plan_effects import (
    known_agent_plan_actions,
    resolve_agent_plan_effects,
)


class AgentPlanEffectsTests(unittest.TestCase):
    def test_every_executable_plan_action_has_a_resolved_effect_contract(self) -> None:
        executable_actions = {
            TEMPLATE_ARCHIVE_ACTION,
            MATERIAL_DELETE_ACTION,
            PROFESSOR_BULK_TAGS_ACTION,
            PROFESSOR_BULK_ARCHIVE_ACTION,
            PROFESSOR_TAG_DELETE_ACTION,
            PROFESSOR_IMPORT_ACTION,
            COMMUNITY_MENTOR_IMPORT_ACTION,
            TEST_EMAIL_SEND_ACTION,
            CRAWL_CANDIDATE_APPROVE_ACTION,
            CRAWL_JOB_RETRY_ACTION,
            CAMPAIGN_CREATE_ACTION,
            CAMPAIGN_SEND_ACTION,
            CAMPAIGN_RESUME_ACTION,
            CAMPAIGN_RESTORE_SEND_ACTION,
            "email.send",
            "email.schedule",
        }
        self.assertEqual(known_agent_plan_actions(), executable_actions)
        for action in executable_actions:
            with self.subTest(action=action):
                effects = resolve_agent_plan_effects(action)
                self.assertEqual(effects.action, action)
                self.assertEqual(effects.resolution, "delegated")
                self.assertTrue(effects.mutates)
                self.assertTrue(effects.impact_scope)
                self.assertTrue(effects.confirmation_required_before_invocation)

    def test_unknown_plan_action_fails_closed(self) -> None:
        effects = resolve_agent_plan_effects("future.action")
        self.assertEqual(effects.external_services, ["unknown"])
        self.assertTrue(effects.cost_may_apply)
        self.assertFalse(effects.reversible)
        self.assertTrue(effects.unknown_external_result_protection)


if __name__ == "__main__":
    unittest.main()
