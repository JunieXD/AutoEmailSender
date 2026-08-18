from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.modules.crawler.v2.routing import (
    ENTRY_EXPANSION_MODE,
    IFRAME_DISCOVERY_REASON,
    PAGINATION_EXPANSION_MODE,
    PageRouteLink,
    V2EntryRoutingPayload,
    V2PaginationRoutingPayload,
    build_page_routing_context,
    build_v2_entry_routing_prompt,
    build_v2_pagination_routing_prompt,
    extract_page_route_controls,
    extract_page_route_links,
    filter_model_selected_route_urls,
    _invoke_structured_routing_phase,
    invoke_v2_page_routing_agent,
)
from app.modules.llm.runtime import (
    ChatCompletionResult,
    ChatCompletionUsage,
    LLMRuntimeAdaptation,
)


class CrawlerV2RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_routing_phase_uses_shared_structured_output_request(self) -> None:
        adaptation = LLMRuntimeAdaptation("chat_completions", None)
        completion = ChatCompletionResult(
            content='{"discovered_urls":[]}',
            usage=ChatCompletionUsage(
                prompt_tokens=3,
                completion_tokens=1,
                total_tokens=4,
                cached_tokens=2,
            ),
        )
        payload = V2EntryRoutingPayload(discovered_urls=[])
        llm_profile = object()
        session_factory = object()

        with patch(
            "app.modules.crawler.v2.routing.request_crawler_structured_completion",
            new=AsyncMock(
                return_value=(completion, payload, "json_schema_strict")
            ),
        ) as request_mock:
            result, attempts, returned_adaptation = await _invoke_structured_routing_phase(
                llm_profile,  # type: ignore[arg-type]
                session_factory=session_factory,  # type: ignore[arg-type]
                adaptation=adaptation,
                phase="entry",
                prompt="prompt",
                result_model=V2EntryRoutingPayload,
            )

        self.assertIs(result, payload)
        self.assertIs(returned_adaptation, adaptation)
        self.assertEqual(attempts[0].usage["input_tokens"], 3)
        request_mock.assert_awaited_once()
        self.assertIs(request_mock.await_args.args[0], session_factory)
        self.assertIs(request_mock.await_args.args[1], llm_profile)
        self.assertIs(request_mock.await_args.args[2], adaptation)
        self.assertIs(
            request_mock.await_args.kwargs["result_model"],
            V2EntryRoutingPayload,
        )

    def test_routing_prompts_keep_entry_selection_and_pagination_separate(self) -> None:
        context = build_page_routing_context(
            title="人员目录",
            page_text="某学院人员目录",
            links=[
                PageRouteLink(
                    url="https://example.edu/list?page=2",
                    label="2",
                    kind="link",
                )
            ],
        )
        entry_prompt = build_v2_entry_routing_prompt(
            university="示例大学",
            school="计算机学院",
            source_url="https://example.edu/list",
            routing_context=context,
        )
        pagination_prompt = build_v2_pagination_routing_prompt(
            university="示例大学",
            school="计算机学院",
            source_url="https://example.edu/list",
            routing_context=context,
        )

        self.assertIn("不依赖固定栏目字眼", entry_prompt)
        self.assertIn("不判断分页", entry_prompt)
        self.assertIn("无论姓名能否点击都算名单", entry_prompt)
        self.assertIn("并列分类", entry_prompt)
        self.assertIn("分类绝不包括同一名单的前后页或页码", entry_prompt)
        self.assertIn("人员类别或单位内部组织", entry_prompt)
        self.assertIn("不要混选多套", entry_prompt)
        self.assertIn("返回网站首页或上级目录", entry_prompt)
        self.assertIn("只判断当前页是否还有同一份人员名单的下一部分", pagination_prompt)
        self.assertIn("同一份人员名单", pagination_prompt)
        self.assertIn("最多选择一个", pagination_prompt)
        self.assertIn("恰好向后推进一页", pagination_prompt)
        self.assertIn("第一页、最后一页、上一页、具体页码跳转", pagination_prompt)
        self.assertIn('"discovered_urls":[]', entry_prompt)
        self.assertIn('"allow_expansion":false', pagination_prompt)

    def test_extract_page_route_links_includes_iframe_and_keeps_spa_route(self) -> None:
        links = extract_page_route_links(
            "https://cs.example.edu/directory/index.htm",
            """
            <a href="page2.htm">下一页</a>
            <iframe title="主名单" src="https://welcome.example.edu/#/teacher/computer?page=1"></iframe>
            """,
        )

        self.assertEqual(
            [(link.kind, link.label, link.url) for link in links],
            [
                ("link", "下一页", "https://cs.example.edu/directory/page2.htm"),
                (
                    "iframe",
                    "主名单",
                    "https://welcome.example.edu/#/teacher/computer?page=1",
                ),
            ],
        )

    def test_extract_page_route_links_keeps_all_labels_for_same_pagination_url(self) -> None:
        links = extract_page_route_links(
            "https://example.edu/list.htm",
            """
            <a href="list/12.htm">2</a>
            <a href="list/12.htm">下页</a>
            <a href="list/1.htm">13</a>
            <a href="list/1.htm">尾页</a>
            """,
        )

        self.assertEqual(
            [(link.url, link.label) for link in links],
            [
                ("https://example.edu/list/12.htm", "2 | 下页"),
                ("https://example.edu/list/1.htm", "13 | 尾页"),
            ],
        )

    def test_extract_page_route_controls_exposes_non_url_next_control(self) -> None:
        controls = extract_page_route_controls(
            """
            <ul class="pager">
              <li title="上一页" tabindex="0" class="pager-prev disabled"><a>‹</a></li>
              <li title="2" tabindex="0" class="pager-item"><a>2</a></li>
              <li title="下一页" tabindex="0" class="pager-next"><a aria-label="right icon"></a></li>
            </ul>
            """
        )

        self.assertEqual(len(controls), 2)
        self.assertEqual(controls[0].title, "2")
        self.assertEqual(controls[1].title, "下一页")
        self.assertEqual(controls[1].control_id, "control-2")
        self.assertEqual(controls[1].aria_label, "right icon")

    def test_extract_page_route_controls_exposes_javascript_form_pagination(self) -> None:
        html = """
        <a class="filter" href="javascript:;">A</a>
        <a class="Next" href="javascript:document.forms['pager'].page.value=2;document.forms['pager'].submit();">2</a>
        <a class="Next" href="javascript:document.forms['pager'].action.value='NextPage';document.forms['pager'].submit();">下页</a>
        <a href="/directory/profile.htm">张三</a>
        """

        controls = extract_page_route_controls(html)
        links = extract_page_route_links("https://example.edu/directory", html)

        self.assertEqual([control.text for control in controls], ["A", "2", "下页"])
        self.assertEqual(controls[-1].tag, "a")
        self.assertEqual(controls[-1].class_tokens, ("Next",))
        self.assertEqual(
            [link.url for link in links],
            ["https://example.edu/directory/profile.htm"],
        )

    def test_filter_selected_urls_requires_page_evidence_and_same_main_domain(self) -> None:
        same_school_sibling = "https://faculty.csu.edu.cn/list?page=2"
        other_school = "https://faculty.other.edu.cn/list?page=2"
        links = [
            PageRouteLink(url=same_school_sibling, label="2", kind="link"),
            PageRouteLink(url=other_school, label="2", kind="link"),
        ]

        accepted = filter_model_selected_route_urls(
            [
                same_school_sibling,
                other_school,
                "https://faculty.csu.edu.cn/hallucinated",
            ],
            links=links,
            source_url="https://cse.csu.edu.cn/faculty",
            start_url="https://cse.csu.edu.cn/faculty",
        )

        self.assertEqual(accepted, [same_school_sibling])

    def test_pagination_payload_requires_boolean_to_match_urls(self) -> None:
        V2PaginationRoutingPayload.model_validate(
            {
                "allow_expansion": True,
                "pagination_urls": ["https://example.edu/p2"],
                "pagination_control_id": None,
            }
        )
        V2PaginationRoutingPayload.model_validate(
            {
                "allow_expansion": True,
                "pagination_urls": [],
                "pagination_control_id": "control-2",
            }
        )
        with self.assertRaises(ValidationError):
            V2PaginationRoutingPayload.model_validate(
                {
                    "allow_expansion": True,
                    "pagination_urls": [],
                    "pagination_control_id": None,
                }
            )
        with self.assertRaises(ValidationError):
            V2PaginationRoutingPayload.model_validate(
                {
                    "allow_expansion": False,
                    "pagination_urls": ["https://example.edu/p2"],
                    "pagination_control_id": None,
                }
            )
        with self.assertRaises(ValidationError):
            V2PaginationRoutingPayload.model_validate(
                {
                    "allow_expansion": False,
                    "pagination_urls": [],
                    "pagination_control_id": "control-2",
                }
            )
        with self.assertRaises(ValidationError):
            V2PaginationRoutingPayload.model_validate(
                {
                    "allow_expansion": True,
                    "pagination_urls": [
                        "https://example.edu/p2",
                        "https://example.edu/p12",
                    ],
                    "pagination_control_id": None,
                }
            )

    def test_filter_selected_pagination_urls_keeps_only_one_model_choice(self) -> None:
        page2 = "https://example.edu/p2"
        last = "https://example.edu/p12"
        accepted = filter_model_selected_route_urls(
            [page2, last],
            links=[
                PageRouteLink(url=page2, label="下一页", kind="link"),
                PageRouteLink(url=last, label="末页", kind="link"),
            ],
            source_url="https://example.edu/list",
            start_url="https://example.edu/list",
            max_results=1,
        )

        self.assertEqual(accepted, [page2])

    def test_filter_selected_entry_urls_does_not_apply_pagination_cap(self) -> None:
        urls = [
            "https://faculty.csu.edu.cn/list-a",
            "https://faculty.csu.edu.cn/list-b",
        ]
        accepted = filter_model_selected_route_urls(
            urls,
            links=[PageRouteLink(url=url, label=url, kind="link") for url in urls],
            source_url="https://cse.csu.edu.cn/faculty",
            start_url="https://cse.csu.edu.cn/faculty",
        )

        self.assertEqual(accepted, urls)

    async def test_entry_page_runs_entry_selection_then_pagination(self) -> None:
        adaptation = LLMRuntimeAdaptation("chat_completions", None)
        iframe_url = "https://welcome.example.edu/#/teacher/computer"
        page2_url = "https://cs.example.edu/index.htm?page=2"
        phase_mock = AsyncMock(
            side_effect=[
                (
                    V2EntryRoutingPayload(discovered_urls=[iframe_url]),
                    [],
                    adaptation,
                ),
                (
                    V2PaginationRoutingPayload(
                        allow_expansion=True,
                        pagination_urls=[page2_url],
                        pagination_control_id=None,
                    ),
                    [],
                    adaptation,
                ),
            ]
        )
        with patch(
            "app.modules.crawler.v2.routing._invoke_structured_routing_phase",
            new=phase_mock,
        ):
            result = await invoke_v2_page_routing_agent(
                object(),
                session_factory=object(),
                university="示例大学",
                school="计算机学院",
                start_url="https://cs.example.edu/index.htm",
                source_url="https://cs.example.edu/index.htm",
                title="人员入口",
                page_text="人员入口",
                page_html=(
                    f'<iframe src="{iframe_url}"></iframe>'
                    f'<a href="{page2_url}">2</a>'
                ),
                expansion_mode=ENTRY_EXPANSION_MODE,
                adaptation=adaptation,
            )

        self.assertEqual(phase_mock.await_count, 2)
        self.assertEqual(result.discovered_urls, [iframe_url])
        self.assertEqual(result.entry_discovery_reasons[iframe_url], IFRAME_DISCOVERY_REASON)
        self.assertTrue(result.allow_expansion)
        self.assertEqual(result.pagination_urls, [page2_url])

    async def test_pagination_page_cannot_run_entry_selection_again(self) -> None:
        adaptation = LLMRuntimeAdaptation("chat_completions", None)
        phase_mock = AsyncMock(
            return_value=(
                V2PaginationRoutingPayload(
                    allow_expansion=False,
                    pagination_urls=[],
                    pagination_control_id=None,
                ),
                [],
                adaptation,
            )
        )
        with patch(
            "app.modules.crawler.v2.routing._invoke_structured_routing_phase",
            new=phase_mock,
        ):
            result = await invoke_v2_page_routing_agent(
                object(),
                session_factory=object(),
                university="示例大学",
                school="计算机学院",
                start_url="https://cs.example.edu/list",
                source_url="https://cs.example.edu/list?page=2",
                title="人员名单",
                page_text="人员名单第二页",
                page_html='<a href="/other-list">另一名单</a>',
                expansion_mode=PAGINATION_EXPANSION_MODE,
                adaptation=adaptation,
            )

        phase_mock.assert_awaited_once()
        self.assertEqual(phase_mock.await_args.kwargs["phase"], "pagination")
        self.assertEqual(result.discovered_urls, [])
        self.assertFalse(result.allow_expansion)

    async def test_pagination_page_can_select_one_real_non_url_control(self) -> None:
        adaptation = LLMRuntimeAdaptation("chat_completions", None)
        phase_mock = AsyncMock(
            return_value=(
                V2PaginationRoutingPayload(
                    allow_expansion=True,
                    pagination_urls=[],
                    pagination_control_id="control-2",
                ),
                [],
                adaptation,
            )
        )
        with patch(
            "app.modules.crawler.v2.routing._invoke_structured_routing_phase",
            new=phase_mock,
        ):
            result = await invoke_v2_page_routing_agent(
                object(),
                session_factory=object(),
                university="示例大学",
                school="计算机学院",
                start_url="https://example.edu/list",
                source_url="https://example.edu/list",
                title="人员名单",
                page_text="人员名单第一页",
                page_html=(
                    '<li title="1" tabindex="0" class="pager-item active"><a>1</a></li>'
                    '<li title="下一页" tabindex="0" class="pager-next"><a></a></li>'
                ),
                expansion_mode=PAGINATION_EXPANSION_MODE,
                adaptation=adaptation,
            )

        self.assertTrue(result.allow_expansion)
        assert result.pagination_control is not None
        self.assertEqual(result.pagination_control.control_id, "control-2")
        self.assertEqual(result.pagination_control.title, "下一页")


if __name__ == "__main__":
    unittest.main()
