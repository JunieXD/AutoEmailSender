from __future__ import annotations

import re
import unittest
from dataclasses import replace

from app.services.crawler_chunking import (
    ChunkingConfig,
    build_page_chunks,
    estimate_tokens,
    fingerprint_page,
    html_to_link_enriched_text,
    split_chunk_content,
)


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

    def test_link_enriched_text_keeps_small_table_rows_as_lightweight_paragraphs(self) -> None:
        html = """
        <table>
          <tr class="person"><td><a href="/zhang.htm">张三</a></td><td>zhang@example.edu</td></tr>
          <tr class="person"><td><a href="/li.htm">李四</a></td><td>li@example.edu</td></tr>
        </table>
        """

        structured = html_to_link_enriched_text("https://cs.example.edu/list", html, "")
        flat = html_to_link_enriched_text(
            "https://cs.example.edu/list",
            html,
            "",
            preserve_structure_boundaries=False,
        )

        self.assertIn("zhang@example.edu\n\n[李四]", structured)
        self.assertNotIn("zhang@example.edu\n\n[李四]", flat)
        self.assertNotIn("<tr", structured)
        self.assertNotIn("person", structured)
        self.assertNotIn("AES_BLOCK_BOUNDARY", structured)
        self.assertLessEqual(estimate_tokens(structured), estimate_tokens(flat) + 2)

    def test_link_enriched_text_keeps_description_items_as_record_boundaries(self) -> None:
        html = """
        <dl class="faculty-list">
          <dd><a href="/zhang.htm">张三 教授</a><p>研究方向：数据库</p></dd>
          <dd><a href="/li.htm">李四 教授</a><p>研究方向：人工智能</p></dd>
          <dd><a href="/wang.htm">王五 教授</a><p>研究方向：软件工程</p></dd>
        </dl>
        """

        structured = html_to_link_enriched_text("https://cs.example.edu/list", html, "")

        self.assertIn("研究方向：数据库\n\n[李四 教授]", structured)
        self.assertIn("研究方向：人工智能\n\n[王五 教授]", structured)
        self.assertNotIn("AES_BLOCK_BOUNDARY", structured)

    def test_link_enriched_text_keeps_unlabeled_url_inside_linkless_record(self) -> None:
        cards = "".join(
            "<div class='grid-item'><div class='person-card'>"
            f"<p>教师{index} 教授</p><p>研究方向：人工智能</p>"
            f"<a class='card-link' href='/teacher/{index}.htm'></a>"
            "</div></div>"
            for index in range(3)
        )
        html = (
            "<header><a href='/logo'></a></header>"
            "<nav><ul><li><span>首页</span><a href='/home'></a></li></ul></nav>"
            f"<main><div class='row'>{cards}</div></main>"
        )

        structured = html_to_link_enriched_text("https://cs.example.edu/list", html, "")

        for index in range(3):
            self.assertIn(
                f"[无文字链接](https://cs.example.edu/teacher/{index}.htm)",
                structured,
            )
        self.assertNotIn("https://cs.example.edu/logo", structured)
        self.assertNotIn("https://cs.example.edu/home", structured)

    def test_link_enriched_text_does_not_add_empty_links_when_record_has_labeled_link(self) -> None:
        html = """
        <dl>
          <dd>
            <a href="/zhang.htm"></a>
            <a href="/zhang.htm">张三 教授</a>
            <a href="/unrelated.htm"></a>
          </dd>
        </dl>
        """

        structured = html_to_link_enriched_text("https://cs.example.edu/list", html, "")

        self.assertIn("[张三 教授](https://cs.example.edu/zhang.htm)", structured)
        self.assertNotIn("无文字链接", structured)
        self.assertNotIn("unrelated.htm", structured)

    def test_recursive_dense_split_prefers_dom_blocks_without_dropping_overlap_fallback(self) -> None:
        rows = "".join(
            "<tr><td><a href='/teacher/{index}.htm'>教师{index}</a></td>"
            "<td>teacher{index}@example.edu</td><td>人工智能与软件工程</td></tr>".format(index=index)
            for index in range(24)
        )
        parent = build_page_chunks(
            source_url="https://cs.example.edu/faculty/list.htm",
            html=f"<table>{rows}</table>",
            text="",
            config=ChunkingConfig(
                target_tokens=5000,
                soft_max_tokens=5500,
                hard_max_tokens=6000,
                single_chunk_max_tokens=6000,
                min_balanced_target_tokens=4000,
                max_balanced_target_tokens=5000,
            ),
        )[0]

        children = split_chunk_content(
            source_url=parent.source_url,
            content=parent.content,
            parent_chunk_id=parent.chunk_id,
            page_fingerprint=parent.page_fingerprint,
            split_depth=1,
            split_reason="candidate_count_exceeded",
            config=ChunkingConfig(retry_split_target_tokens=100, retry_split_overlap_tokens=15),
        )

        self.assertGreater(len(children), 1)
        for index in range(24):
            link = f"[教师{index}](https://cs.example.edu/teacher/{index}.htm)"
            email = f"teacher{index}@example.edu"
            self.assertTrue(
                any(link in child.content and email in child.content for child in children),
                f"row {index} was split across child chunks",
            )
        self.assertTrue(any(child.overlap_prefix for child in children[1:]))

    def test_recursive_dense_split_preserves_description_item_records(self) -> None:
        items = "".join(
            "<dd>"
            f"<a href='/teacher/{index}.htm'>教师{index} 教授</a>"
            f"<p>研究方向：人工智能与软件工程，记录标记{index}</p>"
            f"<a href='/teacher/{index}.htm'>个人主页</a>"
            "</dd>"
            for index in range(16)
        )
        parent = build_page_chunks(
            source_url="https://cs.example.edu/faculty/list.htm",
            html=f"<dl>{items}</dl>",
            text="",
            config=ChunkingConfig(
                target_tokens=5000,
                soft_max_tokens=5500,
                hard_max_tokens=6000,
                single_chunk_max_tokens=6000,
                min_balanced_target_tokens=4000,
                max_balanced_target_tokens=5000,
            ),
        )[0]

        children = split_chunk_content(
            source_url=parent.source_url,
            content=parent.content,
            parent_chunk_id=parent.chunk_id,
            page_fingerprint=parent.page_fingerprint,
            split_depth=1,
            split_reason="candidate_count_exceeded",
            config=ChunkingConfig(retry_split_target_tokens=100),
        )

        profile_pattern = re.compile(r"https://cs\.example\.edu/teacher/(\d+)\.htm")
        self.assertLessEqual(
            max(len(set(profile_pattern.findall(child.content))) for child in children),
            10,
        )
        for index in range(16):
            self.assertTrue(
                any(
                    f"[教师{index} 教授](https://cs.example.edu/teacher/{index}.htm)" in child.content
                    and f"记录标记{index}" in child.content
                    for child in children
                ),
                f"description item {index} was split across child chunks",
            )

    def test_build_page_chunks_exposes_iframe_source_as_a_link(self) -> None:
        chunks = build_page_chunks(
            source_url="https://cs.example.edu/faculty/index.htm",
            html='<main>人员目录</main><iframe title="人员名单" src="https://welcome.example.edu/#/teachers"></iframe>',
            text="人员目录",
            config=ChunkingConfig(),
        )

        self.assertEqual(len(chunks), 1)
        self.assertIn(
            "[iframe: 人员名单](https://welcome.example.edu/#/teachers)",
            chunks[0].content,
        )

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

    def test_dense_retry_splits_dominant_multiline_record_group(self) -> None:
        navigation = "\n\n".join(f"导航栏目{index}" for index in range(6))
        records = "\n".join(
            "\n".join(
                (
                    f"[教师{index}](https://cs.example.edu/teacher/{index}.htm)",
                    "职称：教授",
                    "研究方向：人工智能、计算机视觉、软件工程与数据治理",
                    f"[个人主页](https://cs.example.edu/teacher/{index}.htm)",
                )
            )
            for index in range(16)
        )
        content = f"{navigation}\n\n{records}"

        drafts = split_chunk_content(
            source_url="https://cs.example.edu/faculty",
            content=content,
            parent_chunk_id="c1",
            page_fingerprint="p",
            split_depth=1,
            split_reason="candidate_count_exceeded",
            config=ChunkingConfig(),
        )

        profile_pattern = re.compile(r"https://cs\.example\.edu/teacher/(\d+)\.htm")
        per_child_counts = [len(set(profile_pattern.findall(draft.content))) for draft in drafts]
        all_profiles = {
            profile
            for draft in drafts
            for profile in profile_pattern.findall(draft.content)
        }
        self.assertEqual(all_profiles, {str(index) for index in range(16)})
        self.assertLessEqual(max(per_child_counts), 10)
        self.assertLess(max(draft.token_estimate for draft in drafts), estimate_tokens(content))

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

    def test_replays_candidate_dense_markdown_with_limited_retry_overlap(self) -> None:
        from app.services.crawler_chunking import split_chunk_content

        source_url = "https://faculty.hust.edu.cn/teachers/index.htm"
        markdown = "\n".join(
            "[教师{index}](https://faculty.hust.edu.cn/teacher/{index}.htm) "
            "研究方向：人工智能、计算机视觉与智能系统，本科教学与科研指导。".format(index=index)
            for index in range(150)
        )
        link_pattern = re.compile(r"\]\((https://faculty\.hust\.edu\.cn/[^)]+)\)")

        def replay(config: ChunkingConfig) -> tuple[int, int, int]:
            pending = list(
                build_page_chunks(
                    source_url=source_url,
                    html="",
                    text=markdown,
                    config=config,
                )
            )
            saved_links: list[str] = []
            node_count = 0

            while pending:
                chunk = pending.pop(0)
                node_count += 1
                links = link_pattern.findall(chunk.content)
                if len(links) > 10:
                    children = split_chunk_content(
                        source_url=source_url,
                        content=chunk.content,
                        parent_chunk_id=chunk.chunk_id,
                        page_fingerprint=chunk.page_fingerprint,
                        split_depth=chunk.split_depth + 1,
                        split_reason="candidate_count_exceeded",
                        config=config,
                    )
                    if children:
                        pending.extend(children)
                        continue
                saved_links.extend(links)

            unique_count = len(set(saved_links))
            return unique_count, node_count, len(saved_links) - unique_count

        overlap_15 = replay(ChunkingConfig())
        overlap_30 = replay(replace(ChunkingConfig(), retry_split_overlap_tokens=30))

        self.assertEqual(overlap_15[0], 150)
        self.assertEqual(overlap_30[0], 150)
        self.assertLessEqual(overlap_15[1], overlap_30[1])
        self.assertLessEqual(overlap_15[2], overlap_30[2])

    def test_fingerprint_page_is_stable(self) -> None:
        self.assertEqual(fingerprint_page("  张三\n李四  "), fingerprint_page("张三 李四"))

    def test_estimate_tokens_counts_chinese_and_ascii(self) -> None:
        self.assertGreaterEqual(estimate_tokens("张三教授 email@example.edu"), 6)


if __name__ == "__main__":
    unittest.main()
