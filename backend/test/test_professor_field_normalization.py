from __future__ import annotations

import unittest

from app.modules.professors.public import (
    RECENT_PAPERS_MAX_ITEMS,
    normalize_recent_papers,
    normalize_research_direction,
)


class ProfessorFieldNormalizationTests(unittest.TestCase):
    def test_normalize_research_direction_list_to_chinese_semicolon(self) -> None:
        self.assertEqual(
            normalize_research_direction([" 大模型 ", "", "智能体", "信息抽取"]),
            "大模型；智能体；信息抽取",
        )

    def test_normalize_recent_papers_string_split_trim_dedupe_and_cap(self) -> None:
        raw = "Paper A|Paper B；Paper A\nPaper C;Paper D|Paper E|Paper F|Paper G|Paper H|Paper I"
        self.assertEqual(
            normalize_recent_papers(raw),
            ["Paper A", "Paper B", "Paper C", "Paper D", "Paper E", "Paper F", "Paper G", "Paper H"],
        )
        self.assertEqual(RECENT_PAPERS_MAX_ITEMS, 8)

    def test_normalize_recent_papers_list_keeps_order_and_caps(self) -> None:
        raw = [f" Paper {index} " for index in range(1, 12)]
        self.assertEqual(normalize_recent_papers(raw), [f"Paper {index}" for index in range(1, 9)])


if __name__ == "__main__":
    unittest.main()
