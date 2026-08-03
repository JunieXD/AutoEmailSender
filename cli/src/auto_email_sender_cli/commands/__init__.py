from auto_email_sender_cli.commands.communications import communications_app
from auto_email_sender_cli.commands.drafts import drafts_app
from auto_email_sender_cli.commands.plans import plans_app
from auto_email_sender_cli.commands.professors import professors_app
from auto_email_sender_cli.commands.resources import (
    identities_app,
    llm_profiles_app,
    materials_app,
    templates_app,
)

__all__ = [
    "communications_app",
    "drafts_app",
    "identities_app",
    "llm_profiles_app",
    "materials_app",
    "plans_app",
    "professors_app",
    "templates_app",
]
