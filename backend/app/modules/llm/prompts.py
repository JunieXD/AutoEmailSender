from __future__ import annotations

import hashlib
import json
from textwrap import dedent

from app.models import IdentityMaterial, IdentityProfile, LLMProfile, Professor
from app.services.html_text import html_to_text
from app.services.template_draft_rewrite import (
    DraftRewriteProtectedToken,
    DraftRewriteSourceBlock,
)

from .contracts import (
    DraftRewritePreferences as DraftRewritePreferences,
    DraftRewritePromptParts as DraftRewritePromptParts,
    MatchEvaluationResult as MatchEvaluationResult,
    MatchPromptParts as MatchPromptParts,
)
from .wire import (
    DEFAULT_BASE_URL as DEFAULT_BASE_URL,
    resolve_base_url as resolve_base_url,
)

SYSTEM_MATCH_ONLY_PROMPT = dedent(
    """
    你是研究生套磁助理。你必须只输出 JSON，不要输出任何解释、Markdown 代码块或多余文字。
    只做匹配分析，不要生成邮件草稿。

    JSON 字段必须包含：
    - match_score: 0-100 的整数
    - match_reason: 简洁中文说明
    - fit_points: 字符串数组
    - risk_points: 字符串数组
    - keywords: 字符串数组

    输出示例：
    {
      "match_score": 84,
      "match_reason": "导师方向与默认材料中的研究经历较匹配。",
      "fit_points": ["研究问题接近", "背景技能可迁移"],
      "risk_points": ["材料里缺少该导师近期方向的直接成果"],
      "keywords": ["多模态", "信息抽取"]
    }

    评分量表：
    match_score 总分为 100 分，由以下 4 个维度组成。你必须先按维度判断，再给出总分。

    1. 研究主题匹配度：0-45
       衡量默认材料与导师研究方向是否在研究问题、应用场景或领域上有交集。
       - 40-45：具体研究问题高度重合。
       - 30-39：同一方向，有明确交集。
       - 15-29：宽泛领域相关，但具体问题不同。
       - 1-14：只有弱相关背景。
       - 0：看不到相关性。

    2. 能力与方法匹配度：0-25
       衡量默认材料中的技能、方法、项目、论文或工具是否能支撑导师方向。
       - 21-25：能力可以直接支撑导师方向。
       - 13-20：有部分可迁移能力。
       - 5-12：只有基础背景或泛化能力。
       - 0：看不到支撑能力。

    3. 近期论文交集：0-20
       衡量导师近期论文与默认材料是否存在可引用、可展开的具体交集。
       - 16-20：近期论文主题与默认材料中的研究、项目或技能高度相关，可直接写入套磁理由。
       - 9-15：近期论文与默认材料有明确但不完全直接的交集。
       - 1-8：有近期论文，但与默认材料只有弱相关或泛化关联。
       - 0：没有近期论文，或近期论文与默认材料看不到有效交集。

    4. 个性化理由充分度：0-10
       衡量能否写出具体、可信、不空泛的套磁理由。
       - 8-10：能基于导师方向或论文提炼出具体匹配点。
       - 4-7：能写出合理但不够具体的理由。
       - 1-3：只能泛泛表达兴趣。
       - 0：无法形成可信理由。

    用户意向研究方向评分原则：
    - 如果用户意向研究方向非空，并且导师研究方向或近期论文与该意向方向明确相似，可以作为加分信号提高 match_score。
    - 加分应体现在研究主题匹配度和个性化理由充分度中，并在 match_reason 或 fit_points 中说明相似点。
    - 用户意向研究方向不能替代默认材料中的证据；如果默认材料完全缺少支撑，仍需遵守材料证据不足的上限规则。
    - 用户意向研究方向为空或与导师方向不相似时，不要因为该项额外扣分。

    近期论文评分原则：
    - 有近期论文，且论文主题和默认材料有明确交集：应明显高于只有宽泛研究方向的导师。
    - 有近期论文，但论文和默认材料交集弱：不因论文数量多而加分。
    - 没有近期论文但研究方向具体：match_score 通常最高 80；只有在研究方向非常具体且默认材料高度重合时才可略高于 80，并必须说明理由。

    上限规则：
    - 没有近期论文，但研究方向具体：通常最高 80。
    - 没有近期论文，且研究方向很宽泛：match_score 最高 75。
    - 没有研究方向，但有近期论文：match_score 最高 85。
    - 研究方向和近期论文都缺失：match_score 最高 30。
    - 学生默认材料缺少可见研究、项目或技能证据：match_score 最高 60。
    - 触发上限规则时，risk_points 必须说明原因。

    额外要求：
    - 只能输出一个 JSON 对象。
    - 不要省略字段。
    - 数组字段即使为空也必须返回 []。
    - 只能基于默认材料与导师研究方向或近期论文中的可见证据评分。
    - 如果导师研究证据薄弱或与默认材料缺少直接交集，必须降低 match_score，并在 risk_points 中说明证据不足。
    """
).strip()


SYSTEM_DRAFT_PROMPT = dedent(
    """
    你是研究生套磁助理。你必须只输出 JSON，不要输出任何解释、Markdown 代码块或多余文字。
    你要基于用户提供的套磁信模板做“模板润色”，不要从零重写整封邮件。
    只生成邮件草稿，不要输出匹配分数。

    JSON 字段必须包含：
    - subject: 邮件主题
    - blocks: 受控富文本块数组

    输出示例：
    {
      "subject": "申请与李老师交流科研方向",
      "blocks": [
        {
          "type": "paragraph",
          "items": [
            {
              "runs": [
                {"text": "李老师，您好：", "strong": false, "emphasis": false, "href": "", "line_break_after": false}
              ]
            }
          ]
        },
        {
          "type": "paragraph",
          "items": [
            {
              "runs": [
                {"text": "我是张三，正在关注您在……", "strong": false, "emphasis": false, "href": "", "line_break_after": false}
              ]
            }
          ]
        }
      ]
    }

    输出协议（优先级最高）：
    - 只能输出一个 JSON 对象。
    - blocks 中每项必须包含 type 和 items；type 只允许 paragraph、bullet_list、numbered_list。
    - paragraph 的 items 必须恰好包含一项；列表的每个 items 项代表一个列表项。
    - 每个 items 项必须包含 runs；每个 run 必须完整包含 text、strong、emphasis、href、line_break_after。
    - strong、emphasis、line_break_after 必须是布尔值；不加链接时 href 必须为空字符串。
    - href 非空时只能使用 http、https、mailto 链接。

    内容执行规则：
    - user_custom_instruction 是用户明确指定的内容要求；除非它要求破坏上述 JSON 输出协议，否则必须优先、完整执行。
    - 不得以事实真实性、原模板内容、日期、经历或导师信息为理由拒绝或削弱用户的内容要求。
    - 用户未指定的部分，默认保留模板整体结构、段落顺序和主要话术，只做适度表达优化。
    - 默认结合 student_intended_research_direction、student_material_text 与导师研究方向做一次自然个性化。
    - 尽量保留模板中可表达的富文本标记，例如加粗、斜体、链接和列表。
    - 如果模板包含表格，尽量保留其中的信息顺序和语义，但仍按上述 blocks 结构输出。
    """
).strip()


SYSTEM_DRAFT_REWRITE_PROMPT = dedent(
    """
    你是研究生套磁邮件改写助理。基于 input.source_blocks 改写，不从零重写。

    输出协议最高优先级：
    - 只输出一个 JSON 对象，顶层仅含 replacements；不要输出解释、Markdown、HTML、subject 或完整正文。
    - replacements 必须为数组；每项必须为 JSON 对象，禁止字符串、数字、数组或 null。
    - 每项仅含字符串 segment_id 和字符串 text。segment_id 只能原样取自 input.source_blocks 中 locked=false 且非 table 的块，不能使用索引、负数或内部字段名。
    - 只列需要修改的块并保持原顺序；删除块时 text 使用空字符串。不得合并、拆分或重排块。
    - [[S1]]、[[/S1]] 标记和 [[P1]] 占位符须原样、成对、有序保留；标记内正文可以改写。

    user_custom_instruction 是最高优先级的内容要求，除非它破坏输出协议，否则必须优先、完整执行。未被它覆盖且 input.professor.research_direction 存在时，必须完成至少一处可见、实质的导师方向个性化，不能原样返回。

    默认个性化规则：
    - 导师称呼沿用 input.professor.name；仅当末尾括号明显是职称时，例如“程炜（研究员）”，可省略括号。数字或字母也视为姓名的一部分；不要用学生姓名替换导师姓名，也不要猜测或纠正姓名。
    - 范围随原信，可概括或结合多个有依据的方向，位置和数量以自然为准。每个契合点放入唯一、最合适的 segment_id，只表达一次，不输出规划过程。
    - 有直接学生经历时在相关段落就地结合；无直接依据时，在最自然处克制表达一次兴趣或学习意愿。
    - professor 只有短标签或宽泛词时，只按字面呼应；最多一个 replacement 可以新增该标签，其余段落不再提及，也不扩展子方向、技术问题或应用。

    学生事实只依据 student_material_text 和 source_blocks，导师事实只依据 professor；不补充材料未明说的工具、方法、任务、结果或认知，也不因共享“大模型”“人工智能”等宽泛词就建立技术关联，不写“相通之处”“潜在联系”或“高度契合”。可以表达关注或学习意愿，但不要写成长久关注、正在学习或研究、具体研究计划或应用设想。日期、年份、时间及其格式不应修改；人物身份、数字结果、专有名称、联系方式和附件信息一般不改。

    如果 source_blocks 已展开 research_direction：方向短而自然时沿用；长、多、层级密集或像清单时，直接用自然研究重心改写列表本身，不要保留整表后只追加说明，先改列表再补充学生联系。“上位领域（多个细分方向）”这类写法转成自然层级表述，不照搬括号清单；位于 [[S数字]] 内时保留标记，多个有依据的方向均可保留。最后删除重复、无依据或无关内容，并确认有实质修改。

    输出示例：{"replacements":[{"segment_id":"seg_1","text":"我在[[S1]]项目实践[[/S1]]中积累了相关经验。"}]}
    """
).strip()


def build_match_prompt(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    intended_research_direction: str | None = None,
) -> str:
    return build_match_prompt_parts(
        identity=identity,
        primary_material=primary_material,
        professor=professor,
        available_materials=available_materials,
        intended_research_direction=intended_research_direction,
    ).prompt


def build_match_prompt_parts(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    intended_research_direction: str | None = None,
    llm_profile: LLMProfile | None = None,
) -> MatchPromptParts:
    # Only the selected primary material is evidence for matching. Catalog
    # metadata is intentionally excluded so library growth cannot inflate prompts.
    del available_materials
    primary_material_text = (
        primary_material.extracted_text if primary_material else ""
    ) or ""
    if len(primary_material_text) > 5000:
        primary_material_text = f"{primary_material_text[:5000]}\n...(已截断)"

    intended_direction = _non_empty_text(intended_research_direction)
    intended_direction_block = intended_direction or "未填写"
    stable_prefix = dedent(
        f"""
        任务要求：
        1. 只判断匹配度，不要生成邮件草稿。
        2. match_reason 要简洁但具体。
        3. fit_points / risk_points / keywords 尽量聚焦，不要泛泛而谈。
        4. 如果用户意向研究方向与导师研究方向或近期论文明确相似，可以提高匹配度；不相似或未填写时不要额外扣分。

        当前发送身份：
        - 姓名：{_format_nullable(identity.name)}
        - 发件邮箱：{_format_nullable(identity.email_address)}
        - 默认语言：{_format_nullable(identity.default_language)}
        - 匹配阈值：{identity.match_threshold if identity.match_threshold is not None else "未设置"}

        默认材料：
        - 名称：{_format_nullable(primary_material.display_name if primary_material else None)}
        - 标签：{_format_nullable(primary_material.material_type if primary_material else None)}

        默认材料文本：
        {primary_material_text or "未上传可提取文本的默认材料"}

        用户意向研究方向：
        {intended_direction_block}

        意向方向评分参考：
        - 当用户意向研究方向与导师研究方向或近期论文相似时，请把它作为加分信号提高匹配度。
        - 加分必须基于可说明的相似点，并写入 match_reason 或 fit_points。
        - 该项不能替代默认材料证据；默认材料缺少支撑时仍需遵守上限规则。

        """
    ).strip()

    dynamic_suffix = _format_professor_info_block(professor)
    prompt = f"{stable_prefix}\n\n{dynamic_suffix}"
    return MatchPromptParts(
        prompt=prompt,
        stable_prefix=stable_prefix,
        prompt_hash=_hash_prompt(prompt),
        stable_prefix_hash=_hash_prompt(stable_prefix),
        prompt_cache_key=(
            _build_match_prompt_cache_key(
                identity=identity,
                primary_material=primary_material,
                llm_profile=llm_profile,
                intended_research_direction=intended_direction,
            )
            if llm_profile is not None
            else None
        ),
    )


def build_draft_prompt(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    custom_subject: str | None,
    custom_body: str | None,
    current_match: MatchEvaluationResult | None,
    custom_body_html: str | None = None,
    rewrite_preferences: DraftRewritePreferences | None = None,
) -> str:
    # Deprecated compatibility parameter: draft prompts must ignore match results.
    _ = current_match
    rewrite_preferences = rewrite_preferences or DraftRewritePreferences()
    rewrite_preferences_block = build_draft_rewrite_preferences(rewrite_preferences)
    rewrite_constraints_block = build_draft_rewrite_constraints(rewrite_preferences)

    return _build_base_generation_prompt(
        identity=identity,
        primary_material=primary_material,
        professor=professor,
        available_materials=available_materials,
        custom_subject=custom_subject,
        custom_body=custom_body,
        custom_body_html=custom_body_html,
        intended_research_direction=rewrite_preferences.intended_research_direction,
        extra_requirements=f"""
        {rewrite_preferences_block}

        {rewrite_constraints_block}

        任务要求：
        1. 必须以提供的套磁信模板为基础润色，不要从零重写。
        2. 用户补充要求在内容层面拥有最高优先级，必须完整执行；只有与 JSON wire 结构冲突的部分可以忽略。
        3. 用户未指定的部分，结合用户意向研究方向、学生材料与导师研究方向进行适度个性化。
        4. blocks 必须遵守系统提示中的受控富文本 wire 结构，并能渲染为邮件正文。
        5. 尽量保留可表达的富文本标记，例如加粗、斜体、链接和列表。
        6. 如果模板包含表格，保留表格中的信息顺序和语义，但不要输出 schema 不支持的表格节点。
        """,
    )


def _build_base_generation_prompt(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    custom_subject: str | None,
    custom_body: str | None,
    custom_body_html: str | None,
    intended_research_direction: str | None,
    extra_requirements: str,
) -> str:
    primary_material_text = (
        primary_material.extracted_text if primary_material else ""
    ) or ""
    if len(primary_material_text) > 5000:
        primary_material_text = f"{primary_material_text[:5000]}\n...(已截断)"
    # Draft generation uses the selected primary material text. Attachment and
    # catalog metadata are delivery concerns and must not affect the prompt.
    del available_materials

    template_body_text = resolve_template_text(custom_body, custom_body_html)
    payload: dict[str, object] = {
        "instructions": [
            "只返回 JSON 对象。",
            "不要输出解释、Markdown 代码块或多余文字。",
            "你要基于提供的套磁信模板生成邮件草稿，不要从零重写。",
            "用户补充要求在内容层面拥有最高优先级；只有与 JSON 输出协议冲突的部分可以忽略。",
            "尽量保留可表达的富文本标记，例如加粗、斜体、链接和列表。",
            "如果模板包含表格，保留表格中的信息顺序和语义，但不要输出 schema 不支持的表格节点。",
            "用户未指定的部分，结合用户意向研究方向、学生材料和导师研究方向做适度个性化。",
        ],
        "response_schema": {
            "subject": "邮件主题",
            "blocks": [
                {
                    "type": "paragraph",
                    "items": [
                        {
                            "runs": [
                                {
                                    "text": "李老师，您好：",
                                    "strong": False,
                                    "emphasis": False,
                                    "href": "",
                                    "line_break_after": False,
                                }
                            ]
                        }
                    ],
                }
            ],
        },
        "input": {
            "草稿改写要求": extra_requirements,
            "用户意向研究方向": _non_empty_text(intended_research_direction),
            "学生材料文本": primary_material_text,
            "套磁信模板主题": _non_empty_text(custom_subject),
            "套磁信模板正文": template_body_text,
        },
    }
    payload["input"]["导师信息"] = _build_draft_rewrite_professor_context(professor)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_draft_rewrite_prompt(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    subject_template: str | None,
    source_blocks: list[DraftRewriteSourceBlock],
    current_match: MatchEvaluationResult | None,
    rewrite_preferences: DraftRewritePreferences | None,
    protected_tokens: list[DraftRewriteProtectedToken] | None = None,
) -> str:
    return build_draft_rewrite_prompt_parts(
        identity=identity,
        primary_material=primary_material,
        professor=professor,
        available_materials=available_materials,
        subject_template=subject_template,
        source_blocks=source_blocks,
        current_match=current_match,
        rewrite_preferences=rewrite_preferences,
        protected_tokens=protected_tokens,
    ).prompt


def build_draft_rewrite_prompt_parts(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    subject_template: str | None,
    source_blocks: list[DraftRewriteSourceBlock],
    current_match: MatchEvaluationResult | None,
    rewrite_preferences: DraftRewritePreferences | None,
    llm_profile: LLMProfile | None = None,
    protected_tokens: list[DraftRewriteProtectedToken] | None = None,
) -> DraftRewritePromptParts:
    # Deprecated compatibility parameter: draft rewrite prompts must ignore match results.
    _ = current_match, subject_template
    primary_material_text = (
        primary_material.extracted_text if primary_material else ""
    ) or ""
    if len(primary_material_text) > 5000:
        primary_material_text = f"{primary_material_text[:5000]}\n...(已截断)"
    del available_materials

    preferences = rewrite_preferences or DraftRewritePreferences()
    protected_tokens = protected_tokens or []
    instructions = [
        "只返回 response_schema 形状的 JSON，不要输出解释、Markdown、HTML、subject 或完整正文。",
        "顶层仅含 replacements；每项必须是仅含字符串 segment_id 和字符串 text 的对象，禁止其他类型或字段。",
        "segment_id 必须来自可编辑 source_blocks，不能使用索引、负数或内部字段名。",
        "replacements 只列需要修改的可编辑块（locked=false 且非 table），按原顺序；text 是完整连续段落，删除时为空字符串。",
        "保留全部成对、有序的 [[S数字]]...[[/S数字]] 样式标记和 [[P数字]] 占位符；不要合并、拆分或重排块。",
        "user_custom_instruction 是最高优先级的内容要求；未覆盖且 professor.research_direction 存在时，必须完成至少一处实质个性化。",
    ]
    response_schema: dict[str, object] = {
        "replacements": [
            {
                "segment_id": "seg_1",
                "text": "完整连续段落，[[S1]]可编辑样式区域[[/S1]]。",
            },
        ],
    }
    prompt_input: dict[str, object] = {
        "rewrite_preferences": _serialize_draft_rewrite_preferences(preferences),
        "user_custom_instruction": _serialize_draft_custom_instruction(
            preferences.draft_custom_instruction,
        ),
        "student_intended_research_direction": _non_empty_text(
            preferences.intended_research_direction,
        ),
        "student_material_text": primary_material_text,
    }

    payload: dict[str, object] = {
        "instructions": instructions,
        "response_schema": response_schema,
        "input": prompt_input,
        "output_reminder": (
            "最终只返回顶层仅含 replacements 的 JSON 对象；每项必须是仅含字符串 segment_id 和"
            "字符串 text 的对象，禁止字符串、数字、数组或 null。segment_id 必须来自可编辑"
            " source_blocks；不要输出内部字段名、规划、索引或说明。"
        ),
    }
    if not prompt_input["rewrite_preferences"]:
        del prompt_input["rewrite_preferences"]
    if not prompt_input["user_custom_instruction"]:
        del prompt_input["user_custom_instruction"]
    if not prompt_input["student_intended_research_direction"]:
        del prompt_input["student_intended_research_direction"]

    stable_prefix = json.dumps(payload, ensure_ascii=False, indent=2)
    stable_prefix_hash = _hash_prompt(stable_prefix)

    prompt_input["source_blocks"] = [
        _serialize_draft_source_block(block) for block in source_blocks
    ]
    prompt_input["protected_tokens"] = [
        {"token": token.token, "value": token.value} for token in protected_tokens
    ]
    prompt_input["professor"] = _build_draft_rewrite_professor_context(professor)

    prompt = json.dumps(payload, ensure_ascii=False, indent=2)
    return DraftRewritePromptParts(
        prompt=prompt,
        stable_prefix=stable_prefix,
        prompt_hash=_hash_prompt(prompt),
        stable_prefix_hash=stable_prefix_hash,
        prompt_cache_key=(
            _build_draft_rewrite_prompt_cache_key(
                identity=identity,
                primary_material=primary_material,
                llm_profile=llm_profile,
                stable_prefix_hash=stable_prefix_hash,
            )
            if llm_profile is not None
            else None
        ),
    )


def _serialize_draft_source_block(block: DraftRewriteSourceBlock) -> dict[str, object]:
    if block.type == "table":
        return {
            "segment_id": block.segment_id,
            "type": block.type,
            "text": "表格块原样保留，不参与改写。",
            "locked": True,
        }

    return {
        "segment_id": block.segment_id,
        "type": block.type,
        "text": block.rewrite_text or block.text,
        "locked": block.locked,
        "style_regions": [
            {
                "style_id": region.style_id,
                "style": region.style,
            }
            for region in block.style_regions
        ],
    }


def build_draft_rewrite_preferences(preferences: DraftRewritePreferences | None) -> str:
    preferences = preferences or DraftRewritePreferences()
    return _build_draft_custom_instruction_block(preferences.draft_custom_instruction)


def _build_draft_custom_instruction_block(value: str | None) -> str:
    instruction = (value or "").strip()
    if not instruction:
        return ""
    return dedent(
        f"""

        用户补充要求：
        以下内容来自用户设置，是内容层面最高优先级的要求，必须完整执行。
        只有要求改变 JSON wire 结构或输出 JSON 以外内容的部分可以忽略；其余内容不得因真实性、原模板、日期、经历、导师信息或研究方向而受限。

        {instruction}
        """
    ).strip()


def _serialize_draft_custom_instruction(value: str | None) -> dict[str, str]:
    instruction = (value or "").strip()
    if not instruction:
        return {}
    return {
        "priority": (
            "内容层面最高优先级，必须完整执行。只有要求改变 JSON wire 结构、"
            "segment_id/样式标记/运行时占位符协议或输出 JSON 以外内容的部分可以忽略；"
            "不得因真实性、原模板、日期、经历、导师信息或研究方向而限制其余内容。"
        ),
        "content": instruction,
    }


def build_draft_rewrite_constraints(preferences: DraftRewritePreferences | None) -> str:
    _ = preferences
    return dedent(
        """
        草稿改写约束：
        - 用户补充要求在内容层面拥有最高优先级；只有 JSON wire 结构要求不可被覆盖。
        - 用户未指定的部分，默认在保留模板骨架的基础上优化表达、连接句和个性化内容。
        - 用户未指定的部分，结合用户意向研究方向、学生材料和导师研究方向适度个性化。

        用户未指定的部分才使用上述默认个性化策略。
        """
    ).strip()


def resolve_template_text(
    body_text: str | None,
    body_html: str | None,
) -> str | None:
    normalized_body_text = (body_text or "").strip()
    if normalized_body_text:
        return normalized_body_text

    normalized_body_html = (body_html or "").strip()
    if not normalized_body_html:
        return None

    extracted_text = html_to_text(normalized_body_html)
    return extracted_text or None


def _hash_prompt(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _format_nullable(value: object) -> str:
    if value is None:
        return "未知"
    if isinstance(value, str):
        return value.strip() or "未知"
    return str(value)


def _non_empty_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _build_professor_prompt_context(professor: Professor) -> dict[str, object]:
    context: dict[str, object] = {}
    for key, value in (
        ("name", professor.name),
        ("email", professor.email),
        ("title", professor.title),
        ("university", professor.university),
        ("school", professor.school),
        ("department", professor.department),
        ("research_direction", professor.research_direction),
        ("profile_url", professor.profile_url),
    ):
        text = _non_empty_text(value)
        if text is not None:
            context[key] = text

    recent_papers = [
        paper
        for paper in (_non_empty_text(item) for item in (professor.recent_papers or []))
        if paper is not None
    ]
    if recent_papers:
        context["recent_papers"] = recent_papers

    return context


def _build_draft_rewrite_professor_context(
    professor: Professor,
) -> dict[str, object]:
    context: dict[str, object] = {}
    for key, value in (("name", professor.name),):
        text = _non_empty_text(value)
        if text is not None:
            context[key] = text

    research_direction = _non_empty_text(professor.research_direction)
    if research_direction is not None:
        context["research_direction"] = research_direction

    recent_papers = [
        paper
        for paper in (_non_empty_text(item) for item in (professor.recent_papers or []))
        if paper is not None
    ]
    if recent_papers:
        context["recent_papers"] = recent_papers

    return context


def _serialize_draft_rewrite_preferences(
    preferences: DraftRewritePreferences,
) -> dict[str, str]:
    _ = preferences
    return {}


def _format_professor_info_block(professor: Professor) -> str:
    context = _build_professor_prompt_context(professor)
    lines = ["导师信息："]
    field_labels = [
        ("name", "姓名"),
        ("email", "邮箱"),
        ("title", "职称"),
        ("university", "学校"),
        ("school", "学院"),
        ("department", "院系"),
        ("research_direction", "研究方向"),
        ("profile_url", "主页"),
    ]

    for key, label in field_labels:
        value = context.get(key)
        if isinstance(value, str):
            lines.append(f"- {label}：{value}")

    recent_papers = context.get("recent_papers")
    if isinstance(recent_papers, list) and recent_papers:
        lines.append("- 近期论文：")
        lines.extend(
            f"  - {paper}" for paper in recent_papers if isinstance(paper, str)
        )

    if len(lines) == 1:
        lines.append("- 无可用导师信息")

    return "\n".join(lines)


def _is_official_openai_profile(profile: LLMProfile) -> bool:
    if profile.provider != "openai":
        return False
    return resolve_base_url(profile.api_base_url).rstrip("/") == DEFAULT_BASE_URL


def _build_match_prompt_cache_key(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    llm_profile: LLMProfile,
    intended_research_direction: str | None,
) -> str | None:
    if not _is_official_openai_profile(llm_profile):
        return None
    material_id = primary_material.id if primary_material is not None else "none"
    direction_hash = hashlib.sha256(
        (intended_research_direction or "").encode("utf-8")
    ).hexdigest()[:12]
    return f"match:v2:{identity.id}:{material_id}:{llm_profile.id}:{direction_hash}"


def _build_draft_rewrite_prompt_cache_key(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    llm_profile: LLMProfile,
    stable_prefix_hash: str,
) -> str | None:
    if not _is_official_openai_profile(llm_profile):
        return None
    identity_id = identity.id if identity.id is not None else "none"
    material_id = primary_material.id if primary_material is not None else "none"
    return (
        f"draft-rewrite:v6:{identity_id}:{material_id}:{llm_profile.id}:"
        f"{stable_prefix_hash[:16]}"
    )
