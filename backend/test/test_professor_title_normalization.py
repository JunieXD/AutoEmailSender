from __future__ import annotations

import unittest

from app.modules.professors.public import normalize_professor_title


class ProfessorTitleNormalizationTests(unittest.TestCase):
    def test_keeps_allowed_single_titles(self) -> None:
        self.assertEqual(normalize_professor_title("教授"), "教授")
        self.assertEqual(normalize_professor_title("特聘研究员"), "特聘研究员")

    def test_extracts_single_title_from_composite_string(self) -> None:
        self.assertEqual(normalize_professor_title("教授、博导"), "教授")
        self.assertEqual(normalize_professor_title("副教授/硕导"), "副教授")

    def test_discards_non_title_honor_labels(self) -> None:
        self.assertIsNone(normalize_professor_title("国家级领军人才"))
        self.assertEqual(
            normalize_professor_title("国家级领军人才、教授、博导"),
            "教授",
        )

    def test_uses_priority_when_multiple_allowed_titles_appear(self) -> None:
        self.assertEqual(normalize_professor_title("研究员、副教授"), "副教授")
        self.assertEqual(normalize_professor_title("特聘研究员、讲师"), "讲师")


if __name__ == "__main__":
    unittest.main()
