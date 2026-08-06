from __future__ import annotations

import unittest


class CommunicationsModuleBoundaryTest(unittest.TestCase):
    def test_public_facade_reexports_cross_domain_contracts(self) -> None:
        from app.modules.communications import addresses, events, ingestion, public, transport
        from app.modules.communications.imap import sync
        from app.modules.communications.test_compose import runtime, schemas

        self.assertIs(public.normalize_email_address, addresses.normalize_email_address)
        self.assertIs(public.CommunicationEvent, events.CommunicationEvent)
        self.assertIs(public.EmailLogIngestRecord, ingestion.EmailLogIngestRecord)
        self.assertIs(public.MailRuntimeError, transport.MailRuntimeError)
        self.assertIs(public.poll_for_replies_once, sync.poll_for_replies_once)
        self.assertIs(public.sync_identity_imap_once, sync.sync_identity_imap_once)
        self.assertIs(
            public.sync_workspace_professor_replies,
            sync.sync_workspace_professor_replies,
        )
        self.assertIs(public.TestComposeThreadRead, schemas.TestComposeThreadRead)
        self.assertIs(public.build_test_compose_thread, runtime.build_test_compose_thread)


if __name__ == "__main__":
    unittest.main()
