from __future__ import annotations

import ast
import importlib
from pathlib import Path
import subprocess
import sys
import unittest


LEGACY_MODULE_OWNERS = {
    "app.api.test_compose": "app.modules.communications.test_compose.api",
    "app.schemas.test_compose": "app.modules.communications.test_compose.schemas",
    "app.services.test_compose_runtime": "app.modules.communications.test_compose.runtime",
    "app.services.email_addresses": "app.modules.communications.addresses",
    "app.services.communication_events": "app.modules.communications.events",
    "app.services.email_log_ingestion": "app.modules.communications.ingestion",
    "app.services.mail_runtime": "app.modules.communications.transport",
    "app.services.smtp_error_explanations": "app.modules.communications.smtp_errors",
    "app.services.imap_errors": "app.modules.communications.imap.errors",
    "app.services.imap_message_fetcher": "app.modules.communications.imap.fetcher",
    "app.services.imap_rate_limiter": "app.modules.communications.imap.rate_limiter",
    "app.services.imap_sync_state": "app.modules.communications.imap.state",
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


class CommunicationsModuleCompatibilityTest(unittest.TestCase):
    def test_all_legacy_exports_reference_communications_owners(self) -> None:
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
        from app.modules.communications import addresses, events, ingestion, public, transport
        from app.modules.communications.test_compose import runtime, schemas

        self.assertIs(public.normalize_email_address, addresses.normalize_email_address)
        self.assertIs(public.CommunicationEvent, events.CommunicationEvent)
        self.assertIs(public.EmailLogIngestRecord, ingestion.EmailLogIngestRecord)
        self.assertIs(public.MailRuntimeError, transport.MailRuntimeError)
        self.assertIs(public.TestComposeThreadRead, schemas.TestComposeThreadRead)
        self.assertIs(public.build_test_compose_thread, runtime.build_test_compose_thread)

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
