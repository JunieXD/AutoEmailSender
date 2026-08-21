from __future__ import annotations

import unittest
from unittest.mock import patch

from app.core.windows_event_loop import ensure_windows_proactor_event_loop_policy


class WindowsEventLoopPolicyTests(unittest.TestCase):
    def test_sets_proactor_policy_on_windows_when_current_policy_differs(self) -> None:
        class FakeProactorPolicy:
            pass

        current_policy = object()

        with (
            patch(
                "app.core.windows_event_loop.platform.system", return_value="Windows"
            ),
            patch(
                "app.core.windows_event_loop.asyncio.get_event_loop_policy",
                return_value=current_policy,
            ),
            patch(
                "app.core.windows_event_loop.asyncio.WindowsProactorEventLoopPolicy",
                FakeProactorPolicy,
                create=True,
            ),
            patch(
                "app.core.windows_event_loop.asyncio.set_event_loop_policy"
            ) as set_policy,
        ):
            changed = ensure_windows_proactor_event_loop_policy()

        self.assertTrue(changed)
        set_policy.assert_called_once()
        self.assertIsInstance(set_policy.call_args.args[0], FakeProactorPolicy)

    def test_does_not_reset_policy_when_windows_policy_is_already_proactor(
        self,
    ) -> None:
        class FakeProactorPolicy:
            pass

        current_policy = FakeProactorPolicy()

        with (
            patch(
                "app.core.windows_event_loop.platform.system", return_value="Windows"
            ),
            patch(
                "app.core.windows_event_loop.asyncio.get_event_loop_policy",
                return_value=current_policy,
            ),
            patch(
                "app.core.windows_event_loop.asyncio.WindowsProactorEventLoopPolicy",
                FakeProactorPolicy,
                create=True,
            ),
            patch(
                "app.core.windows_event_loop.asyncio.set_event_loop_policy"
            ) as set_policy,
        ):
            changed = ensure_windows_proactor_event_loop_policy()

        self.assertFalse(changed)
        set_policy.assert_not_called()

    def test_does_not_change_policy_on_non_windows(self) -> None:
        with (
            patch("app.core.windows_event_loop.platform.system", return_value="Linux"),
            patch(
                "app.core.windows_event_loop.asyncio.set_event_loop_policy"
            ) as set_policy,
        ):
            changed = ensure_windows_proactor_event_loop_policy()

        self.assertFalse(changed)
        set_policy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
