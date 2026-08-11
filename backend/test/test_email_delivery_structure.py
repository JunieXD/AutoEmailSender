from __future__ import annotations

import ast
import inspect
import textwrap
import unittest

from app.modules.communications import transport
from app.modules.workspace.tasks import delivery


class EmailDeliveryStructureTests(unittest.TestCase):
    def test_preparation_precedes_claim_and_smtp_uses_only_prepared_message(self) -> None:
        source = inspect.getsource(delivery.dispatch_email_task)

        prepare_index = source.index("mail_runtime.prepare_email")
        claim_index = source.index("_claim_prepared_delivery")
        smtp_index = source.index("mail_runtime.send_prepared_email")

        self.assertLess(prepare_index, claim_index)
        self.assertLess(claim_index, smtp_index)
        self.assertNotIn("mail_runtime.send_email(", source)

    def test_final_commit_transaction_only_awaits_database_and_fault_gate_operations(
        self,
    ) -> None:
        tree = ast.parse(
            textwrap.dedent(inspect.getsource(delivery._finalize_delivery_attempt))
        )
        awaited_calls = {
            self._call_name(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
        }

        self.assertEqual(
            awaited_calls,
            {
                "attach_delivery_observations",
                "ensure_delivery_email_log",
                "record_operation_log",
                "release_delivery_observation_candidates",
                "session.commit",
                "session.execute",
                "session.get",
                "session.rollback",
                "wait_at_fault_point",
            },
        )

    def test_smtp_transport_does_not_wait_for_quit_after_data_acceptance(self) -> None:
        source = inspect.getsource(transport._send_email_sync)
        send_index = source.index("server.send_message(message)")
        after_send = source[send_index:]

        self.assertNotIn("server.quit", after_send)
        self.assertIn("server.close", after_send)

    @staticmethod
    def _call_name(call: ast.Call) -> str:
        function = call.func
        if isinstance(function, ast.Name):
            return function.id
        if isinstance(function, ast.Attribute):
            parts = [function.attr]
            value = function.value
            while isinstance(value, ast.Attribute):
                parts.append(value.attr)
                value = value.value
            if isinstance(value, ast.Name):
                parts.append(value.id)
            return ".".join(reversed(parts))
        return "<dynamic>"


if __name__ == "__main__":
    unittest.main()
