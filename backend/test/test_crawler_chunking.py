from __future__ import annotations

import unittest

from app.services.crawler_chunking import ChunkingConfig, build_page_chunks, estimate_tokens, fingerprint_page


class CrawlerChunkingTests(unittest.TestCase):
    def test_build_page_chunks_preserves_links_as_markdown(self) -> None:
        html = """
        <html><body><nav>首页</nav><main>
        <div class="teacher"><a href="/zhang.htm">张三</a><p>研究方向：数据库</p></div>
        <div class="teacher"><a href="https://cs.example.edu/li.htm">李四</a><p>邮箱：li@example.edu</p></div>
        </main><script>alert(1)</script></body></html>
        """
        chunks = build_page_chunks(
            source_url="https://cs.example.edu/faculty/index.htm",
            html=html,
            text="张三\n李四",
            config=ChunkingConfig(),
        )
        self.assertEqual(len(chunks), 1)
        self.assertIn("[张三](https://cs.example.edu/zhang.htm)", chunks[0].content)
        self.assertIn("[李四](https://cs.example.edu/li.htm)", chunks[0].content)
        self.assertNotIn("alert", chunks[0].content)

    def test_build_page_chunks_splits_long_text_with_overlap(self) -> None:
        blocks = "\n".join(f"教师{i} 研究方向 数据库 [详情](https://cs.example.edu/t{i}.htm)" for i in range(80))
        chunks = build_page_chunks(
            source_url="https://cs.example.edu/faculty/index.htm",
            html=f"<main>{''.join(f'<p>{line}</p>' for line in blocks.splitlines())}</main>",
            text=blocks,
            config=ChunkingConfig(target_tokens=120, soft_max_tokens=160, hard_max_tokens=220, overlap_tokens=30),
        )
        self.assertGreater(len(chunks), 1)
        self.assertFalse(chunks[0].overlap_prefix)
        self.assertTrue(chunks[0].overlap_suffix)
        self.assertTrue(chunks[1].overlap_prefix)
        self.assertLessEqual(max(chunk.token_estimate for chunk in chunks), 220)

    def test_build_page_chunks_balances_medium_page_into_even_chunks(self) -> None:
        blocks = "\n".join(
            f"教师{i} 研究方向 数据库 人工智能 机器学习 [详情](https://cs.example.edu/t{i}.htm)"
            for i in range(60)
        )
        chunks = build_page_chunks(
            source_url="https://cs.example.edu/faculty/index.htm",
            html=f"<main>{''.join(f'<p>{line}</p>' for line in blocks.splitlines())}</main>",
            text=blocks,
            config=ChunkingConfig(
                target_tokens=1000,
                soft_max_tokens=1400,
                hard_max_tokens=1800,
                overlap_tokens=0,
                single_chunk_max_tokens=1100,
                min_balanced_target_tokens=600,
                max_balanced_target_tokens=1200,
            ),
        )
        token_sizes = [chunk.token_estimate for chunk in chunks]

        self.assertEqual(len(chunks), 2)
        self.assertLessEqual(max(token_sizes) - min(token_sizes), 250)

    def test_build_page_chunks_keeps_small_page_single_chunk(self) -> None:
        blocks = "\n".join(f"教师{i} [详情](https://cs.example.edu/t{i}.htm)" for i in range(20))
        chunks = build_page_chunks(
            source_url="https://cs.example.edu/faculty/index.htm",
            html=f"<main>{''.join(f'<p>{line}</p>' for line in blocks.splitlines())}</main>",
            text=blocks,
            config=ChunkingConfig(
                target_tokens=300,
                soft_max_tokens=500,
                hard_max_tokens=700,
                single_chunk_max_tokens=1000,
            ),
        )

        self.assertEqual(len(chunks), 1)

    def test_build_page_chunks_balanced_target_respects_hard_max(self) -> None:
        blocks = "\n".join(
            f"教师{i} 研究方向 数据库 人工智能 机器学习 大数据治理 [详情](https://cs.example.edu/t{i}.htm)"
            for i in range(120)
        )
        chunks = build_page_chunks(
            source_url="https://cs.example.edu/faculty/index.htm",
            html=f"<main>{''.join(f'<p>{line}</p>' for line in blocks.splitlines())}</main>",
            text=blocks,
            config=ChunkingConfig(
                target_tokens=900,
                soft_max_tokens=1100,
                hard_max_tokens=1300,
                overlap_tokens=20,
                single_chunk_max_tokens=1000,
                min_balanced_target_tokens=500,
                max_balanced_target_tokens=1000,
            ),
        )

        self.assertGreater(len(chunks), 2)
        self.assertLessEqual(max(chunk.token_estimate for chunk in chunks), 1300)


    def test_default_recursive_split_config_supports_dense_small_chunks(self) -> None:
        config = ChunkingConfig()

        self.assertEqual(config.min_split_tokens, 100)
        self.assertEqual(config.retry_split_overlap_tokens, 15)
        self.assertEqual(config.overlap_tokens, 180)
        self.assertEqual(config.max_split_depth, 7)

    def test_split_chunk_content_only_splits_above_one_hundred_tokens(self) -> None:
        from app.services.crawler_chunking import split_chunk_content

        at_minimum = "\n".join(["甲" * 25] * 4)
        above_minimum = "\n".join(["甲" * 25, "乙" * 25, "丙" * 25, "丁" * 26])
        self.assertEqual(estimate_tokens(at_minimum), 100)
        self.assertEqual(estimate_tokens(above_minimum), 101)

        minimum_drafts = split_chunk_content(
            source_url="https://cs.example.edu/faculty",
            content=at_minimum,
            parent_chunk_id="c1",
            page_fingerprint="p",
            split_depth=1,
            split_reason="candidate_count_exceeded",
            config=ChunkingConfig(),
        )
        above_minimum_drafts = split_chunk_content(
            source_url="https://cs.example.edu/faculty",
            content=above_minimum,
            parent_chunk_id="c1",
            page_fingerprint="p",
            split_depth=1,
            split_reason="candidate_count_exceeded",
            config=ChunkingConfig(),
        )

        self.assertEqual(minimum_drafts, [])
        self.assertGreaterEqual(len(above_minimum_drafts), 2)

    def test_split_chunk_content_uses_dynamic_fanout_for_too_many_candidates(self) -> None:
        from app.services.crawler_chunking import split_chunk_content

        content = "\n".join(
            f"教师{i} 研究方向 数据库 人工智能 [详情](https://cs.example.edu/t{i}.htm)"
            for i in range(120)
        )
        drafts = split_chunk_content(
            source_url="https://cs.example.edu/faculty",
            content=content,
            parent_chunk_id="c1",
            page_fingerprint="p",
            split_depth=1,
            split_reason="too_many_candidates",
            config=ChunkingConfig(min_split_tokens=150, overlap_tokens=180),
        )

        self.assertEqual(len(drafts), 10)
        self.assertTrue(all(draft.parent_chunk_id == "c1" for draft in drafts))
        self.assertTrue(all(draft.split_depth == 1 for draft in drafts))
        self.assertGreaterEqual(min(draft.token_estimate for draft in drafts), 150)
        self.assertLessEqual(max(draft.token_estimate for draft in drafts), 500)

    def test_split_chunk_content_caps_retry_overlap_at_fifteen_tokens(self) -> None:
        from app.services.crawler_chunking import split_chunk_content

        content = "\n".join(
            chr(0x4E00 + index) * 5
            for index in range(50)
        )
        drafts = split_chunk_content(
            source_url="https://cs.example.edu/faculty",
            content=content,
            parent_chunk_id="c1",
            page_fingerprint="p",
            split_depth=1,
            split_reason="candidate_count_exceeded",
            config=ChunkingConfig(),
        )

        self.assertEqual(len(drafts), 2)
        first_lines = set(drafts[0].content.splitlines())
        repeated_prefix: list[str] = []
        for line in drafts[1].content.splitlines():
            if line not in first_lines:
                break
            repeated_prefix.append(line)
        repeated_tokens = estimate_tokens("\n".join(repeated_prefix)) if repeated_prefix else 0
        self.assertLessEqual(repeated_tokens, 15)

    def test_split_chunk_content_caps_binary_retry_overlap_when_a_line_exceeds_limit(self) -> None:
        from app.services.crawler_chunking import split_chunk_content

        content = "\n".join(chr(0x4E00 + index) * 20 for index in range(6))
        drafts = split_chunk_content(
            source_url="https://cs.example.edu/faculty",
            content=content,
            parent_chunk_id="c1",
            page_fingerprint="p",
            split_depth=1,
            split_reason="retry_after_parse_error",
            config=ChunkingConfig(),
        )

        self.assertEqual(len(drafts), 2)
        first_lines = set(drafts[0].content.splitlines())
        repeated_prefix: list[str] = []
        for line in drafts[1].content.splitlines():
            if line not in first_lines:
                break
            repeated_prefix.append(line)
        repeated_tokens = estimate_tokens("\n".join(repeated_prefix)) if repeated_prefix else 0
        self.assertLessEqual(repeated_tokens, 15)
    def test_fingerprint_page_is_stable(self) -> None:
        self.assertEqual(fingerprint_page("  张三\n李四  "), fingerprint_page("张三 李四"))

    def test_estimate_tokens_counts_chinese_and_ascii(self) -> None:
        self.assertGreaterEqual(estimate_tokens("张三教授 email@example.edu"), 6)


if __name__ == "__main__":
    unittest.main()
