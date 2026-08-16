from __future__ import annotations

import unittest
from urllib.parse import quote

from app.modules.crawler.v2.profile_url_policy import has_explicit_markdown_link


class CrawlerV2ProfileUrlPolicyTests(unittest.TestCase):
    def test_accepts_direct_and_encoded_url_parameter_proof(self) -> None:
        target = "https://guanwei49.github.io/"
        encoded = quote(quote(target, safe=""), safe="")
        content = (
            "[直接主页](https://profiles.example.net/zhang) "
            f"[校内跳转](https://faculty.example.edu/redirect?home={encoded})"
        )

        self.assertTrue(
            has_explicit_markdown_link(
                content,
                base_url="https://faculty.example.edu/list",
                target_url="https://profiles.example.net/zhang",
            )
        )
        self.assertTrue(
            has_explicit_markdown_link(
                content,
                base_url="https://faculty.example.edu/list",
                target_url=target,
            )
        )

    def test_accepts_exact_fragment_parameter_proof(self) -> None:
        content = (
            "[校内跳转](https://faculty.example.edu/redirect"
            "#home=https%3A%2F%2Fhongyuew.github.io%2F)"
        )

        self.assertTrue(
            has_explicit_markdown_link(
                content,
                base_url="https://faculty.example.edu/list",
                target_url="https://hongyuew.github.io/",
            )
        )

    def test_preserves_url_path_escaping_after_wrapper_decode(self) -> None:
        content = (
            "[校内跳转](https://faculty.example.edu/redirect?"
            "home=https%3A%2F%2Fprofiles.example.net%2Fpeople%2FZhang%2520San)"
        )

        self.assertTrue(
            has_explicit_markdown_link(
                content,
                base_url="https://faculty.example.edu/list",
                target_url="https://profiles.example.net/people/Zhang%20San",
            )
        )

    def test_rejects_partial_substring_and_unrelated_parameter_text(self) -> None:
        content = (
            "[恶意跳转](https://faculty.example.edu/redirect?"
            "home=https%3A%2F%2Fprofiles.example.net%2Fzhang%2Fevil&"
            "note=see-https%3A%2F%2Fprofiles.example.net%2Fzhang)"
        )

        self.assertFalse(
            has_explicit_markdown_link(
                content,
                base_url="https://faculty.example.edu/list",
                target_url="https://profiles.example.net/zhang",
            )
        )

    def test_rejects_non_url_parameter_value(self) -> None:
        self.assertFalse(
            has_explicit_markdown_link(
                "[跳转](https://faculty.example.edu/redirect?home=profiles.example.net)",
                base_url="https://faculty.example.edu/list",
                target_url="https://profiles.example.net/",
            )
        )

    def test_treats_profile_trailing_slash_as_equivalent(self) -> None:
        self.assertTrue(
            has_explicit_markdown_link(
                "[张成伟](http://122.205.5.5:8081/~zhangcw/)",
                base_url="https://ei.hust.edu.cn/xygk/szdw/txgcx.htm",
                target_url="http://122.205.5.5:8081/~zhangcw",
            )
        )


if __name__ == "__main__":
    unittest.main()
