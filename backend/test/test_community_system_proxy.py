from __future__ import annotations

import unittest
from unittest.mock import patch

from app.modules.community.mentors import system_proxy


TARGET_URL = "https://juniexd.github.io/AutoEmailSender-MentorData/latest.json"


class CommunitySystemProxyTests(unittest.TestCase):
    def test_resolves_current_windows_registry_proxy_each_time(self) -> None:
        proxy_settings = [
            {},
            {"https": "http://127.0.0.1:7897"},
            {"https": "http://127.0.0.1:7898"},
        ]
        with (
            patch.object(system_proxy.sys, "platform", "win32"),
            patch.object(
                system_proxy.urllib_request,
                "getproxies_registry",
                side_effect=proxy_settings,
                create=True,
            ),
            patch.object(
                system_proxy.urllib_request,
                "proxy_bypass_registry",
                return_value=False,
                create=True,
            ),
        ):
            self.assertIsNone(system_proxy.resolve_system_proxy(TARGET_URL))
            self.assertEqual(
                system_proxy.resolve_system_proxy(TARGET_URL),
                "http://127.0.0.1:7897",
            )
            self.assertEqual(
                system_proxy.resolve_system_proxy(TARGET_URL),
                "http://127.0.0.1:7898",
            )

    def test_honors_windows_proxy_bypass_rules(self) -> None:
        with patch(
            "app.modules.community.mentors.system_proxy._read_platform_proxy_settings",
            return_value=({"https": "http://127.0.0.1:7897"}, True),
        ):
            self.assertIsNone(system_proxy.resolve_system_proxy(TARGET_URL))

    def test_normalizes_supported_proxy_urls_and_rejects_invalid_values(self) -> None:
        cases = {
            "127.0.0.1:7897": "http://127.0.0.1:7897",
            "HTTP://127.0.0.1:7897/": "http://127.0.0.1:7897",
            "socks5://127.0.0.1:7897": "socks5://127.0.0.1:7897",
            "http://proxy.example": "http://proxy.example",
            "ftp://127.0.0.1:7897": None,
            "http://127.0.0.1:7897/path": None,
            "not a proxy": None,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(system_proxy._normalize_proxy_url(value), expected)


if __name__ == "__main__":
    unittest.main()
