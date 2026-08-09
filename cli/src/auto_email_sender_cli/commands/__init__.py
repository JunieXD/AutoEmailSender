from auto_email_sender_cli.commands.communications import communications_app
from auto_email_sender_cli.commands.communication_groups import communication_groups_app
from auto_email_sender_cli.commands.campaigns import campaigns_app
from auto_email_sender_cli.commands.crawler import crawler_app
from auto_email_sender_cli.commands.diagnostics import diagnostics_app
from auto_email_sender_cli.commands.drafts import drafts_app
from auto_email_sender_cli.commands.deliveries import deliveries_app
from auto_email_sender_cli.commands.enrichment import enrichment_app
from auto_email_sender_cli.commands.insights import dashboard_app, usage_app
from auto_email_sender_cli.commands.matching import matching_app
from auto_email_sender_cli.commands.plans import plans_app
from auto_email_sender_cli.commands.professors import professors_app
from auto_email_sender_cli.commands.settings import settings_app
from auto_email_sender_cli.commands.tasks import tasks_app
from auto_email_sender_cli.commands.test_email import test_email_app
from auto_email_sender_cli.commands.workspaces import workspaces_app
from auto_email_sender_cli.commands.resources import (
    identities_app,
    llm_profiles_app,
    materials_app,
    templates_app,
)

__all__ = [
    "communications_app",
    "communication_groups_app",
    "campaigns_app",
    "crawler_app",
    "dashboard_app",
    "diagnostics_app",
    "drafts_app",
    "deliveries_app",
    "enrichment_app",
    "identities_app",
    "llm_profiles_app",
    "matching_app",
    "materials_app",
    "plans_app",
    "professors_app",
    "settings_app",
    "tasks_app",
    "templates_app",
    "test_email_app",
    "usage_app",
    "workspaces_app",
]
