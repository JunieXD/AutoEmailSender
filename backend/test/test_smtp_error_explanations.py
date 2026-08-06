from __future__ import annotations

import unittest

from app.modules.communications.smtp_errors import explain_smtp_error


class SmtpErrorExplanationTests(unittest.TestCase):
    def test_explains_flow_limit_without_naming_a_provider(self) -> None:
        reason = explain_smtp_error(
            "SMTP 发信失败: (550, b'Requested action aborted: flow over limit')"
        )

        self.assertIn("发送限流", reason)
        self.assertIn("稍后再重试", reason)
        self.assertNotIn("163", reason)

    def test_explains_connection_frequency_limit(self) -> None:
        reason = explain_smtp_error("Connection frequency limited")

        self.assertIn("发送限流", reason)

    def test_explains_authentication_failure_with_authorization_code_hint(self) -> None:
        reason = explain_smtp_error(
            "SMTP 连接失败: (535, b'Error: authentication failed')"
        )

        self.assertIn("身份验证", reason)
        self.assertIn("客户端授权码", reason)
        self.assertIn("邮箱登录密码", reason)

    def test_explains_common_delivery_failures(self) -> None:
        cases = [
            ("554 DT:SPM", "反垃圾"),
            ("553 Mail from must equal authorized user", "发件人地址"),
            ("550 5.1.1 User unknown", "收件地址"),
            ("552 5.2.2 Mailbox full", "邮箱可能已满"),
            ("552 5.3.4 Message size exceeds fixed maximum message size", "附件"),
            ("530 Must issue a STARTTLS command first", "加密连接"),
            ("SSL: CERTIFICATE_VERIFY_FAILED", "加密连接"),
            ("SMTP connection timed out", "超时或中断"),
            ("550 User has no permission", "发信权限"),
            ("451 Requested action aborted: local error in processing", "暂时无法处理"),
        ]

        for raw_error, expected_text in cases:
            with self.subTest(raw_error=raw_error):
                self.assertIn(expected_text, explain_smtp_error(raw_error))

    def test_keeps_unknown_errors_explicitly_uncertain(self) -> None:
        reason = explain_smtp_error("SMTP 发信失败: provider-specific-code")

        self.assertIn("暂未能", reason)
        self.assertIn("原始报错", reason)

    def test_explains_missing_raw_error(self) -> None:
        self.assertIn("没有收到", explain_smtp_error(None))


if __name__ == "__main__":
    unittest.main()
