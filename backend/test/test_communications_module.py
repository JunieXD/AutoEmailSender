from __future__ import annotations

import unittest


class CommunicationsModuleBoundaryTest(unittest.TestCase):
    def test_public_facade_reexports_cross_domain_contracts(self) -> None:
        from app.modules.communications import (
            addresses,
            email_tasks,
            events,
            ingestion,
            public,
            transport,
        )
        from app.modules.communications.imap import message_ingestion, sync
        from app.modules.communications.test_compose import runtime, schemas

        self.assertIs(public.normalize_email_address, addresses.normalize_email_address)
        self.assertIs(public.CommunicationEvent, events.CommunicationEvent)
        self.assertIs(public.EmailLogIngestRecord, ingestion.EmailLogIngestRecord)
        self.assertIs(public.MailRuntimeError, transport.MailRuntimeError)
        self.assertIs(public.load_email_task, email_tasks.load_email_task)
        self.assertIs(public.record_email_task_log, email_tasks.record_email_task_log)
        self.assertIs(
            message_ingestion.TASK_RELATION_OPTIONS,
            email_tasks.EMAIL_TASK_RELATION_OPTIONS,
        )
        self.assertIs(message_ingestion._load_email_task, email_tasks.load_email_task)
        self.assertIs(
            message_ingestion._record_email_task_log, email_tasks.record_email_task_log
        )
        self.assertIs(public.poll_for_replies_once, sync.poll_for_replies_once)
        self.assertIs(public.sync_identity_imap_once, sync.sync_identity_imap_once)
        self.assertIs(
            public.sync_workspace_professor_replies,
            sync.sync_workspace_professor_replies,
        )
        self.assertIs(public.TestComposeThreadRead, schemas.TestComposeThreadRead)
        self.assertIs(
            public.build_test_compose_thread, runtime.build_test_compose_thread
        )


if __name__ == "__main__":
    unittest.main()
