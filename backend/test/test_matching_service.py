from __future__ import annotations

import unittest

from app.models.identity_material import IdentityMaterial
from app.models.identity_profile import IdentityProfile
from app.models.professor import Professor
from app.modules.matching.public import build_draft_email, estimate_match_score


class MatchingServiceTests(unittest.TestCase):
    def test_estimate_match_score_is_deterministic_and_reports_overlapping_terms(self) -> None:
        identity = IdentityProfile(
            name="王同学",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_username="sender@example.com",
            smtp_password="secret",
        )
        identity.current_primary_material = IdentityMaterial(
            identity_id=1,
            display_name="简历",
            original_filename="resume.txt",
            file_path="/tmp/resume.txt",
            size_bytes=10,
            sha256="abc",
            extracted_text="Large Language Models 信息抽取 智能体",
        )
        professor = Professor(
            name="张教授",
            email="zhang@example.edu",
            title="Professor",
            university="Example University",
            school="School of AI",
            department="Computer Science",
            research_direction="Large Language Models and 智能体",
            recent_papers=["Information Extraction with Agents"],
        )

        first_score, first_reason = estimate_match_score(identity, professor)
        second_score, second_reason = estimate_match_score(identity, professor)

        self.assertEqual((first_score, first_reason), (second_score, second_reason))
        self.assertGreaterEqual(first_score, 58)
        self.assertLessEqual(first_score, 98)
        self.assertIn("检测到关键词重合", first_reason)
        self.assertIn("large", first_reason)

    def test_estimate_match_score_uses_conservative_reason_when_no_terms_overlap(self) -> None:
        identity = IdentityProfile(
            name="王同学",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_username="sender@example.com",
            smtp_password="secret",
        )
        professor = Professor(
            name="李教授",
            email="li@example.edu",
            research_direction="Quantum Computing",
            recent_papers=[],
        )

        score, reason = estimate_match_score(identity, professor)

        self.assertGreaterEqual(score, 58)
        self.assertLessEqual(score, 69)
        self.assertEqual(reason, "未检测到明显关键词重合，按基础画像给出保守匹配分数")

    def test_build_draft_email_uses_custom_subject_and_body_without_overwriting_body(self) -> None:
        identity = IdentityProfile(
            name="王同学",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_username="sender@example.com",
            smtp_password="secret",
        )
        professor = Professor(name="张教授", research_direction="智能体")

        subject, body = build_draft_email(
            identity,
            professor,
            custom_subject="自定义主题",
            custom_body="自定义正文",
        )

        self.assertEqual(subject, "自定义主题")
        self.assertEqual(body, "自定义正文")

    def test_build_draft_email_falls_back_to_professor_research_direction(self) -> None:
        identity = IdentityProfile(
            name="王同学",
            email_address="sender@example.com",
            smtp_host="smtp.example.com",
            smtp_username="sender@example.com",
            smtp_password="secret",
        )
        professor = Professor(name="张教授", research_direction="智能体")

        subject, body = build_draft_email(identity, professor)

        self.assertEqual(subject, "申请与张教授老师交流科研方向")
        self.assertIn("我是王同学", body)
        self.assertIn("智能体方向", body)


if __name__ == "__main__":
    unittest.main()
