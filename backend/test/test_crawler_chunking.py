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

    def test_fingerprint_page_is_stable(self) -> None:
        self.assertEqual(fingerprint_page("  张三\n李四  "), fingerprint_page("张三 李四"))

    def test_estimate_tokens_counts_chinese_and_ascii(self) -> None:
        self.assertGreaterEqual(estimate_tokens("张三教授 email@example.edu"), 6)


if __name__ == "__main__":
    unittest.main()
