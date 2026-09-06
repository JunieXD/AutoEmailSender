from __future__ import annotations

import json
import re
import ssl
from datetime import UTC, datetime
from math import ceil
from time import perf_counter
from typing import TYPE_CHECKING, Literal, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from . import transport
from .contracts import (
    ChatCompletionResult as ChatCompletionResult,
    ChatCompletionUsage as ChatCompletionUsage,
    DraftBodyBlockWire as DraftBodyBlockWire,
    DraftBodyItemWire as DraftBodyItemWire,
    DraftBodyRunWire as DraftBodyRunWire,
    DraftGenerationResult as DraftGenerationResult,
    DraftGenerationWireResult as DraftGenerationWireResult,
    DraftRewritePreferences as DraftRewritePreferences,
    DraftRewritePromptParts as DraftRewritePromptParts,
    DraftRewriteResult as DraftRewriteResult,
    DraftRewriteSegmentReplacement as DraftRewriteSegmentReplacement,
    DraftTokenEstimate as DraftTokenEstimate,
    GeneratedDraftContent as GeneratedDraftContent,
    GeneratedMatchEvaluation as GeneratedMatchEvaluation,
    LLMEmptyContentError as LLMEmptyContentError,
    LLMEndpointProtocolError as LLMEndpointProtocolError,
    LLMModelCatalogResult as LLMModelCatalogResult,
    LLMProbeResult as LLMProbeResult,
    LLMRuntimeAdaptation as LLMRuntimeAdaptation,
    LLMRuntimeError as LLMRuntimeError,
    MatchEvaluationResult as MatchEvaluationResult,
    MatchEvaluationWireResult as MatchEvaluationWireResult,
    MatchPromptParts as MatchPromptParts,
)
from .prompts import (
    SYSTEM_DRAFT_PROMPT as SYSTEM_DRAFT_PROMPT,
    SYSTEM_DRAFT_REWRITE_PROMPT as SYSTEM_DRAFT_REWRITE_PROMPT,
    SYSTEM_MATCH_ONLY_PROMPT as SYSTEM_MATCH_ONLY_PROMPT,
    _build_base_generation_prompt as _build_base_generation_prompt,
    _build_draft_custom_instruction_block as _build_draft_custom_instruction_block,
    _build_draft_rewrite_professor_context as _build_draft_rewrite_professor_context,
    _build_draft_rewrite_prompt_cache_key as _build_draft_rewrite_prompt_cache_key,
    _build_match_prompt_cache_key as _build_match_prompt_cache_key,
    _build_professor_prompt_context as _build_professor_prompt_context,
    _format_nullable as _format_nullable,
    _format_professor_info_block as _format_professor_info_block,
    _hash_prompt as _hash_prompt,
    _is_official_openai_profile as _is_official_openai_profile,
    _non_empty_text as _non_empty_text,
    _serialize_draft_custom_instruction as _serialize_draft_custom_instruction,
    _serialize_draft_rewrite_preferences as _serialize_draft_rewrite_preferences,
    _serialize_draft_source_block as _serialize_draft_source_block,
    build_draft_prompt as build_draft_prompt,
    build_draft_rewrite_constraints as build_draft_rewrite_constraints,
    build_draft_rewrite_preferences as build_draft_rewrite_preferences,
    build_draft_rewrite_prompt as build_draft_rewrite_prompt,
    build_draft_rewrite_prompt_parts as build_draft_rewrite_prompt_parts,
    build_match_prompt as build_match_prompt,
    build_match_prompt_parts as build_match_prompt_parts,
    resolve_template_text as resolve_template_text,
)
from .transport import (
    _LLM_CONNECTION_ERROR_MARKERS as _LLM_CONNECTION_ERROR_MARKERS,
    _LLM_RUNTIME_LOG_NAME as _LLM_RUNTIME_LOG_NAME,
    _LLM_TLS_CONNECTION_ERROR_MESSAGE as _LLM_TLS_CONNECTION_ERROR_MESSAGE,
    _LLM_TLS_ERROR_MARKERS as _LLM_TLS_ERROR_MARKERS,
    _LOG_URL_PATTERN as _LOG_URL_PATTERN,
    _build_tls12_context as _build_tls12_context,
    _exception_chain_details as _exception_chain_details,
    _is_tls_bad_record_mac_error as _is_tls_bad_record_mac_error,
    _log_llm_http_exception as _log_llm_http_exception,
    _request_completion_endpoint as _request_completion_endpoint,
    _sanitize_log_text as _sanitize_log_text,
    _send_llm_http_request as _send_llm_http_request,
    _should_retry_with_tls12 as _should_retry_with_tls12,
    format_llm_client_initialization_error as format_llm_client_initialization_error,
    format_llm_runtime_error_for_user as format_llm_runtime_error_for_user,
    sanitize_llm_url as sanitize_llm_url,
)
from .wire import (
    _STEPFUN_OPENAI_BASE_URLS as _STEPFUN_OPENAI_BASE_URLS,
    DEFAULT_BASE_URL as DEFAULT_BASE_URL,
    DEFAULT_LLM_MAX_TOKENS as DEFAULT_LLM_MAX_TOKENS,
    STEPFUN_PROBE_MAX_TOKENS as STEPFUN_PROBE_MAX_TOKENS,
    STRUCTURED_OUTPUT_CONTROL_KEY as STRUCTURED_OUTPUT_CONTROL_KEY,
    _build_responses_content_items as _build_responses_content_items,
    _build_responses_input as _build_responses_input,
    _coerce_token_count as _coerce_token_count,
    _empty_content_error_message as _empty_content_error_message,
    _extract_structured_output_control as _extract_structured_output_control,
    _prepare_strict_json_schema as _prepare_strict_json_schema,
    _structured_output_format as _structured_output_format,
    _validate_strict_json_schema_contract as _validate_strict_json_schema_contract,
    build_chat_completions_payload as build_chat_completions_payload,
    build_endpoint_url as build_endpoint_url,
    build_responses_payload as build_responses_payload,
    compute_duration_ms as compute_duration_ms,
    extract_chat_completion_content as extract_chat_completion_content,
    extract_model_ids as extract_model_ids,
    extract_responses_content as extract_responses_content,
    format_http_error as format_http_error,
    is_deepseek_profile as is_deepseek_profile,
    is_stepfun_profile as is_stepfun_profile,
    parse_completion_usage as parse_completion_usage,
    probe_max_tokens_for_profile as probe_max_tokens_for_profile,
    resolve_base_url as resolve_base_url,
    with_structured_output as with_structured_output,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import IdentityMaterial, IdentityProfile, LLMProfile, Professor
from app.modules.campaigns.public import build_template_context
from app.services.rich_text import (
    normalize_email_html,
    render_rich_text_document,
    text_to_email_html,
)
from app.services.template_draft_rewrite import (
    apply_draft_rewrite_replacements,
    build_draft_rewrite_document,
    render_draft_template_text,
)

DEFAULT_LLM_TEMPERATURE = 0.2


def _endpoint_protocol_switch_reason(error: "LLMEndpointProtocolError") -> str | int:
    if error.response_envelope is not None:
        return error.response_envelope
    if error.status_code is not None:
        return error.status_code
    return "protocol_error"


async def _record_endpoint_protocol_switch(
    session: "AsyncSession",
    *,
    profile: LLMProfile,
    protocol_error: "LLMEndpointProtocolError",
    completion: "ChatCompletionResult",
) -> None:
    reason = _endpoint_protocol_switch_reason(protocol_error)
    attempted_urls = [
        sanitized
        for url in completion.attempted_urls
        if (sanitized := sanitize_llm_url(url)) is not None
    ]
    metadata = {
        "old_endpoint_kind": protocol_error.failed_endpoint_kind,
        "new_endpoint_kind": completion.endpoint_kind,
        "reason": reason,
        "retried": True,
        "endpoint_kind": completion.endpoint_kind,
        "request_url": sanitize_llm_url(completion.request_url),
        "attempted_urls": attempted_urls,
    }
    transport._append_llm_runtime_log(
        json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "event": "llm_endpoint_protocol_switched",
                "provider": profile.provider,
                "model_name": profile.model_name,
                "api_base_url": sanitize_llm_url(
                    resolve_base_url(profile.api_base_url)
                ),
                **metadata,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
    )

    from app.services.operation_logs import record_operation_log

    try:
        async with session.begin_nested():
            await record_operation_log(
                session,
                category="llm",
                event_name="llm.endpoint_protocol_switched",
                entity_type="llm_profile",
                entity_id=str(profile.id) if profile.id is not None else None,
                metadata=metadata,
            )
    except Exception:
        return


StructuredResultT = TypeVar("StructuredResultT", bound=BaseModel)


async def generate_match_evaluation(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    llm_profile: LLMProfile,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    intended_research_direction: str | None = None,
    thinking_extra_body: dict[str, object] | None = None,
    session: "AsyncSession | None" = None,
    adaptation: LLMRuntimeAdaptation | None = None,
) -> GeneratedMatchEvaluation:
    prompt_parts = build_match_prompt_parts(
        identity=identity,
        primary_material=primary_material,
        professor=professor,
        available_materials=available_materials,
        intended_research_direction=intended_research_direction,
        llm_profile=llm_profile,
    )
    payload: dict[str, object] = {
        "model": llm_profile.model_name,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_MATCH_ONLY_PROMPT,
            },
            {
                "role": "user",
                "content": prompt_parts.prompt,
            },
        ],
        "temperature": 0,
        "max_tokens": llm_profile.max_tokens or DEFAULT_LLM_MAX_TOKENS,
    }
    if prompt_parts.prompt_cache_key:
        payload["prompt_cache_key"] = prompt_parts.prompt_cache_key

    completion, wire_result, _structured_mode = await request_structured_completion(
        llm_profile,
        payload,
        MatchEvaluationWireResult,
        extra_body=thinking_extra_body,
        session=session,
        adaptation=adaptation,
    )
    result = MatchEvaluationResult.model_validate(wire_result.model_dump())
    return GeneratedMatchEvaluation(
        result=result,
        usage=completion.usage,
        request_url=completion.request_url,
        attempted_urls=completion.attempted_urls,
        endpoint_kind=completion.endpoint_kind,
        status_code=completion.status_code,
        duration_ms=completion.duration_ms,
        prompt_hash=prompt_parts.prompt_hash,
        stable_prefix_hash=prompt_parts.stable_prefix_hash,
        prompt_cache_key=prompt_parts.prompt_cache_key,
    )


async def generate_draft_content(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    llm_profile: LLMProfile,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    custom_subject: str | None = None,
    custom_body: str | None = None,
    custom_body_html: str | None = None,
    current_match: MatchEvaluationResult | None = None,
    max_tokens: int | None = None,
    rewrite_preferences: DraftRewritePreferences | None = None,
    thinking_extra_body: dict[str, object] | None = None,
    session: "AsyncSession | None" = None,
    adaptation: LLMRuntimeAdaptation | None = None,
) -> GeneratedDraftContent:
    template_html = custom_body_html
    if not template_html and custom_body:
        template_html = text_to_email_html(custom_body).html

    if template_html:
        template_context = build_template_context(identity, professor)
        rewrite_document = build_draft_rewrite_document(template_html, template_context)
        rendered_subject = render_draft_template_text(
            custom_subject, template_context
        ).strip()
        editable_blocks = [
            block
            for block in rewrite_document.blocks
            if block.type != "table" and not block.locked
        ]
        if not editable_blocks:
            rendered = apply_draft_rewrite_replacements(rewrite_document, [])
            return GeneratedDraftContent(
                result=DraftGenerationResult(
                    subject=rendered_subject,
                    body_text=rendered.text,
                    body_html=rendered.html,
                ),
            )
        prompt_parts = build_draft_rewrite_prompt_parts(
            identity=identity,
            primary_material=primary_material,
            professor=professor,
            available_materials=available_materials,
            subject_template=custom_subject,
            source_blocks=rewrite_document.blocks,
            current_match=current_match,
            rewrite_preferences=rewrite_preferences,
            llm_profile=llm_profile,
            protected_tokens=rewrite_document.protected_tokens,
        )
        payload: dict[str, object] = {
            "model": llm_profile.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_DRAFT_REWRITE_PROMPT,
                },
                {
                    "role": "user",
                    "content": prompt_parts.prompt,
                },
            ],
            "temperature": llm_profile.temperature
            if llm_profile.temperature is not None
            else DEFAULT_LLM_TEMPERATURE,
            "max_tokens": max_tokens or DEFAULT_LLM_MAX_TOKENS,
        }
        if prompt_parts.prompt_cache_key is not None:
            payload["prompt_cache_key"] = prompt_parts.prompt_cache_key
        (
            completion,
            rewrite_result,
            _structured_mode,
        ) = await request_structured_completion(
            llm_profile,
            payload,
            DraftRewriteResult,
            extra_body=thinking_extra_body,
            session=session,
            adaptation=adaptation,
        )
        replacements = [item.model_dump() for item in rewrite_result.replacements]
        try:
            rendered = apply_draft_rewrite_replacements(
                rewrite_document,
                replacements,
            )
        except ValueError as exc:
            raise LLMRuntimeError(
                str(exc),
                request_url=completion.request_url,
                attempted_urls=completion.attempted_urls,
                endpoint_kind=completion.endpoint_kind,
                status_code=completion.status_code,
                duration_ms=completion.duration_ms,
                usage=completion.usage,
                raw_content=completion.content,
            ) from exc
        return GeneratedDraftContent(
            result=DraftGenerationResult(
                subject=rendered_subject,
                body_text=rendered.text,
                body_html=rendered.html,
            ),
            usage=completion.usage,
            prompt_hash=prompt_parts.prompt_hash,
            stable_prefix_hash=prompt_parts.stable_prefix_hash,
            prompt_cache_key=prompt_parts.prompt_cache_key,
        )

    prompt = build_draft_prompt(
        identity=identity,
        primary_material=primary_material,
        professor=professor,
        available_materials=available_materials,
        custom_subject=custom_subject,
        custom_body=custom_body,
        custom_body_html=custom_body_html,
        current_match=current_match,
        rewrite_preferences=rewrite_preferences,
    )
    completion, wire_result, _structured_mode = await request_structured_completion(
        llm_profile,
        {
            "model": llm_profile.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_DRAFT_PROMPT,
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "temperature": llm_profile.temperature
            if llm_profile.temperature is not None
            else DEFAULT_LLM_TEMPERATURE,
            "max_tokens": max_tokens or DEFAULT_LLM_MAX_TOKENS,
        },
        DraftGenerationWireResult,
        extra_body=thinking_extra_body,
        session=session,
        adaptation=adaptation,
    )
    result = _draft_generation_wire_to_result(wire_result)
    return GeneratedDraftContent(result=result, usage=completion.usage)


def estimate_draft_content_tokens(
    *,
    identity: IdentityProfile,
    primary_material: IdentityMaterial | None,
    llm_profile: LLMProfile,
    professor: Professor,
    available_materials: list[IdentityMaterial],
    custom_subject: str | None = None,
    custom_body: str | None = None,
    custom_body_html: str | None = None,
    current_match: MatchEvaluationResult | None = None,
    rewrite_preferences: DraftRewritePreferences | None = None,
    max_tokens: int | None = None,
) -> DraftTokenEstimate:
    template_html = custom_body_html
    if not template_html and custom_body:
        template_html = text_to_email_html(custom_body).html

    if template_html:
        template_context = build_template_context(identity, professor)
        rewrite_document = build_draft_rewrite_document(template_html, template_context)
        if not any(
            block.type != "table" and not block.locked
            for block in rewrite_document.blocks
        ):
            return DraftTokenEstimate(
                estimated_prompt_tokens=0,
                estimated_completion_tokens_upper_bound=0,
                estimated_total_tokens_upper_bound=0,
            )
        prompt_parts = build_draft_rewrite_prompt_parts(
            identity=identity,
            primary_material=primary_material,
            professor=professor,
            available_materials=available_materials,
            subject_template=custom_subject,
            source_blocks=rewrite_document.blocks,
            current_match=current_match,
            rewrite_preferences=rewrite_preferences,
            llm_profile=llm_profile,
            protected_tokens=rewrite_document.protected_tokens,
        )
        prompt_text = f"{SYSTEM_DRAFT_REWRITE_PROMPT}\n\n{prompt_parts.prompt}"
    else:
        prompt = build_draft_prompt(
            identity=identity,
            primary_material=primary_material,
            professor=professor,
            available_materials=available_materials,
            custom_subject=custom_subject,
            custom_body=custom_body,
            custom_body_html=custom_body_html,
            current_match=current_match,
            rewrite_preferences=rewrite_preferences,
        )
        prompt_text = f"{SYSTEM_DRAFT_PROMPT}\n\n{prompt}"

    completion_cap = max_tokens or llm_profile.max_tokens or DEFAULT_LLM_MAX_TOKENS
    estimated_prompt_tokens = estimate_text_tokens(prompt_text)
    estimated_total_tokens_upper_bound = estimated_prompt_tokens + completion_cap
    return DraftTokenEstimate(
        estimated_prompt_tokens=estimated_prompt_tokens,
        estimated_completion_tokens_upper_bound=completion_cap,
        estimated_total_tokens_upper_bound=estimated_total_tokens_upper_bound,
    )


async def probe_llm_profile(
    profile: LLMProfile,
    *,
    session: "AsyncSession | None" = None,
    thinking_extra_body: dict[str, object] | None = None,
    adaptation: LLMRuntimeAdaptation | None = None,
) -> LLMProbeResult:
    """Test that the model is reachable. Single-turn ping only.

    Session-owning callers provide a pre-resolved ``adaptation`` so endpoint
    protocol cache misses can be learned and committed with the probe result.
    """

    base_url = resolve_base_url(profile.api_base_url)
    requires_final_text = is_stepfun_profile(profile)
    payload = {
        "model": profile.model_name,
        "messages": [
            {
                "role": "user",
                "content": "只回复 OK",
            },
        ],
        "temperature": 0,
        "max_tokens": probe_max_tokens_for_profile(profile, fallback=8),
    }

    try:
        completion = await request_chat_completion(
            profile,
            payload,
            extra_body=thinking_extra_body,
            allow_empty_content=not requires_final_text,
            session=session,
            adaptation=adaptation,
        )
    except LLMRuntimeError as exc:
        return LLMProbeResult(
            ok=False,
            message=str(exc),
            resolved_base_url=base_url,
            request_url=exc.request_url,
            attempted_urls=exc.attempted_urls,
            endpoint_kind=exc.endpoint_kind,
            status_code=exc.status_code,
            duration_ms=exc.duration_ms,
            consumes_tokens=True,
            response_preview=None,
        )

    preview = (completion.content or "").strip().replace("\n", " ")[:200]
    return LLMProbeResult(
        ok=True,
        message="模型可用性测试成功",
        resolved_base_url=base_url,
        request_url=completion.request_url,
        attempted_urls=completion.attempted_urls,
        endpoint_kind=completion.endpoint_kind,
        status_code=completion.status_code,
        duration_ms=completion.duration_ms,
        consumes_tokens=True,
        prompt_tokens=completion.usage.prompt_tokens if completion.usage else None,
        completion_tokens=completion.usage.completion_tokens
        if completion.usage
        else None,
        total_tokens=completion.usage.total_tokens if completion.usage else None,
        response_preview=preview or None,
    )


async def fetch_llm_profile_models(profile: LLMProfile) -> LLMModelCatalogResult:
    base_url = resolve_base_url(profile.api_base_url)
    timeout_seconds = get_settings().llm_request_timeout_seconds
    timeout = httpx.Timeout(timeout_seconds)
    headers = {
        "Authorization": f"Bearer {profile.api_key}",
        "Content-Type": "application/json",
        "Connection": "close",
    }
    url = build_endpoint_url(base_url, "models")
    start = perf_counter()

    try:
        response = await _send_llm_http_request(
            method="GET",
            profile=profile,
            url=url,
            endpoint_kind="models",
            headers=headers,
            timeout=timeout,
        )
    except (ImportError, ValueError) as exc:
        return LLMModelCatalogResult(
            ok=False,
            message=format_llm_client_initialization_error(exc),
            resolved_base_url=base_url,
            request_url=url,
            attempted_urls=[url],
            endpoint_kind="models",
            duration_ms=compute_duration_ms(start),
            consumes_tokens=False,
        )
    except httpx.TimeoutException:
        return LLMModelCatalogResult(
            ok=False,
            message=f"获取模型列表超时（{timeout_seconds} 秒）",
            resolved_base_url=base_url,
            request_url=url,
            attempted_urls=[url],
            endpoint_kind="models",
            duration_ms=compute_duration_ms(start),
            consumes_tokens=False,
        )
    except (httpx.HTTPError, ssl.SSLError) as exc:
        return LLMModelCatalogResult(
            ok=False,
            message=format_llm_runtime_error_for_user(f"获取模型列表失败: {exc}"),
            resolved_base_url=base_url,
            request_url=url,
            attempted_urls=[url],
            endpoint_kind="models",
            duration_ms=compute_duration_ms(start),
            consumes_tokens=False,
        )

    duration_ms = compute_duration_ms(start)
    if response.status_code >= 400:
        return LLMModelCatalogResult(
            ok=False,
            message=format_http_error(response.status_code, response.text, url),
            resolved_base_url=base_url,
            request_url=url,
            attempted_urls=[url],
            endpoint_kind="models",
            status_code=response.status_code,
            duration_ms=duration_ms,
            consumes_tokens=False,
        )

    try:
        data = response.json()
        models = extract_model_ids(data)
    except (TypeError, ValueError) as exc:
        return LLMModelCatalogResult(
            ok=False,
            message=f"模型列表返回格式无法解析: {exc}",
            resolved_base_url=base_url,
            request_url=url,
            attempted_urls=[url],
            endpoint_kind="models",
            status_code=response.status_code,
            duration_ms=duration_ms,
            consumes_tokens=False,
        )

    selected_model_available = (
        profile.model_name in models if profile.model_name else None
    )
    message = f"已获取 {len(models)} 个模型"
    if profile.model_name:
        if selected_model_available:
            message = f"{message}，当前模型已在列表中"
        else:
            message = f"{message}，但当前模型不在列表中"

    return LLMModelCatalogResult(
        ok=True,
        message=message,
        resolved_base_url=base_url,
        request_url=url,
        attempted_urls=[url],
        endpoint_kind="models",
        status_code=response.status_code,
        duration_ms=duration_ms,
        consumes_tokens=False,
        models=models,
        selected_model_available=selected_model_available,
    )


async def ensure_llm_runtime_adaptation(
    session: "AsyncSession",
    profile: LLMProfile,
    *,
    failed_endpoint_kind: Literal["chat_completions", "responses"] | None = None,
) -> LLMRuntimeAdaptation:
    """Load or learn the endpoint and thinking adaptation for ``profile``.

    Endpoint discovery is serialized per target. The second cache read under
    the lock prevents concurrent requests from issuing duplicate probes.
    """

    from .adaptation.endpoint import (
        endpoint_adaptation_lock,
        endpoint_candidates,
        get_cached_endpoint_kind,
        record_endpoint_adaptation,
    )
    from .adaptation.thinking import ensure_thinking_adaptation

    api_base_url = resolve_base_url(profile.api_base_url)
    endpoint_kind = await get_cached_endpoint_kind(
        session,
        api_base_url=api_base_url,
        model_name=profile.model_name,
    )
    endpoint_attempted_urls: list[str] = []
    if endpoint_kind is None:
        async with endpoint_adaptation_lock(
            api_base_url, profile.model_name
        ) as coordination:
            endpoint_kind = await get_cached_endpoint_kind(
                session,
                api_base_url=api_base_url,
                model_name=profile.model_name,
            )
            if endpoint_kind is None:
                if coordination.learned_endpoint_kind is not None:
                    endpoint_kind = coordination.learned_endpoint_kind
                elif coordination.probe_error is not None:
                    raise coordination.probe_error
                else:
                    try:
                        requires_final_text = is_stepfun_profile(profile)
                        probe_payload = {
                            "model": profile.model_name,
                            "messages": [{"role": "user", "content": "只回复 OK"}],
                            "temperature": 0,
                            "max_tokens": probe_max_tokens_for_profile(
                                profile, fallback=8
                            ),
                        }
                        last_protocol_error: LLMEndpointProtocolError | None = None
                        for candidate in endpoint_candidates(failed_endpoint_kind):
                            try:
                                completion = await _request_completion_endpoint(
                                    profile,
                                    probe_payload,
                                    endpoint_kind=candidate,
                                    allow_empty_content=not requires_final_text,
                                )
                            except LLMEndpointProtocolError as exc:
                                last_protocol_error = exc
                                endpoint_attempted_urls.extend(exc.attempted_urls)
                                continue
                            except LLMRuntimeError as exc:
                                exc.attempted_urls = [
                                    *endpoint_attempted_urls,
                                    *exc.attempted_urls,
                                ]
                                raise
                            endpoint_attempted_urls.extend(completion.attempted_urls)
                            endpoint_kind = candidate
                            await record_endpoint_adaptation(
                                session,
                                api_base_url=api_base_url,
                                model_name=profile.model_name,
                                endpoint_kind=endpoint_kind,
                            )
                            coordination.learned_endpoint_kind = endpoint_kind
                            break
                        if endpoint_kind is None:
                            assert last_protocol_error is not None
                            raise last_protocol_error
                    except Exception as exc:
                        coordination.probe_error = exc
                        raise

    thinking_extra_body = await ensure_thinking_adaptation(
        session,
        profile,
        endpoint_kind=endpoint_kind,
    )
    return LLMRuntimeAdaptation(
        endpoint_kind,
        thinking_extra_body,
        tuple(endpoint_attempted_urls),
    )


def _merge_attempted_urls(*url_lists: list[str] | tuple[str, ...]) -> list[str]:
    merged: list[str] = []
    for urls in url_lists:
        for url in urls:
            if url not in merged:
                merged.append(url)
    return merged


def _merge_protocol_error_attempts(
    protocol_error: LLMEndpointProtocolError,
    error: LLMRuntimeError,
    *additional_url_lists: list[str] | tuple[str, ...],
) -> None:
    error.attempted_urls = [
        *protocol_error.attempted_urls,
        *(url for urls in additional_url_lists for url in urls),
        *error.attempted_urls,
    ]


async def _heal_stale_thinking_adaptation(
    session: "AsyncSession",
    profile: LLMProfile,
    adaptation: LLMRuntimeAdaptation,
    error: LLMRuntimeError,
) -> LLMRuntimeAdaptation | None:
    """Re-learn the thinking extra_body when a live call hits a thinking signal.

    Two failure shapes count: the replay-protocol 400 (classified by
    ``is_thinking_mode_protocol_error``) and an empty-content 200 that arrived
    while a learned disable extra_body was active. The second gate keeps
    models that randomly exhaust their budget on every call from re-probing
    each time. Returns a fresh adaptation to retry with, or ``None`` to
    surface the original error.
    """

    from .adaptation.thinking import (
        ThinkingAdaptationFailed,
        invalidate_thinking_adaptation,
        is_thinking_mode_protocol_error,
    )

    is_protocol_400 = is_thinking_mode_protocol_error(
        error.status_code or 0,
        str(error),
    )
    is_stale_empty_content = (
        isinstance(error, LLMEmptyContentError)
        and adaptation.thinking_extra_body is not None
    )
    if not (is_protocol_400 or is_stale_empty_content):
        return None
    invalidated = await invalidate_thinking_adaptation(
        session,
        api_base_url=resolve_base_url(profile.api_base_url),
        model_name=profile.model_name,
        endpoint_kind=adaptation.endpoint_kind,
        expected_extra_body=adaptation.thinking_extra_body,
    )
    if not invalidated:
        return None
    try:
        return await ensure_llm_runtime_adaptation(session, profile)
    except (LLMRuntimeError, ThinkingAdaptationFailed):
        return None


async def request_chat_completion(
    profile: LLMProfile,
    payload: dict[str, object],
    *,
    extra_body: dict[str, object] | None = None,
    allow_empty_content: bool = False,
    session: "AsyncSession | None" = None,
    adaptation: LLMRuntimeAdaptation | None = None,
) -> ChatCompletionResult:
    if session is not None:
        active_adaptation = adaptation or await ensure_llm_runtime_adaptation(
            session, profile
        )
        try:
            completion = await _request_completion_endpoint(
                profile,
                payload,
                endpoint_kind=active_adaptation.endpoint_kind,
                extra_body=active_adaptation.thinking_extra_body,
                allow_empty_content=allow_empty_content,
            )
            completion.attempted_urls = _merge_attempted_urls(
                active_adaptation.endpoint_attempted_urls,
                completion.attempted_urls,
            )
            return completion
        except LLMEndpointProtocolError as protocol_error:
            from .adaptation.endpoint import invalidate_endpoint_adaptation
            from .adaptation.thinking import invalidate_thinking_adaptation

            api_base_url = resolve_base_url(profile.api_base_url)
            await invalidate_endpoint_adaptation(
                session,
                api_base_url=api_base_url,
                model_name=profile.model_name,
                failed_endpoint_kind=active_adaptation.endpoint_kind,
            )
            await invalidate_thinking_adaptation(
                session,
                api_base_url=api_base_url,
                model_name=profile.model_name,
                endpoint_kind=active_adaptation.endpoint_kind,
                expected_extra_body=active_adaptation.thinking_extra_body,
            )
            try:
                retry_adaptation = await ensure_llm_runtime_adaptation(
                    session,
                    profile,
                    failed_endpoint_kind=active_adaptation.endpoint_kind,
                )
            except LLMRuntimeError as retry_error:
                _merge_protocol_error_attempts(protocol_error, retry_error)
                raise
            try:
                completion = await _request_completion_endpoint(
                    profile,
                    payload,
                    endpoint_kind=retry_adaptation.endpoint_kind,
                    extra_body=retry_adaptation.thinking_extra_body,
                    allow_empty_content=allow_empty_content,
                )
            except LLMRuntimeError as retry_error:
                _merge_protocol_error_attempts(
                    protocol_error,
                    retry_error,
                    retry_adaptation.endpoint_attempted_urls,
                )
                raise
            completion.attempted_urls = _merge_attempted_urls(
                protocol_error.attempted_urls,
                retry_adaptation.endpoint_attempted_urls,
                completion.attempted_urls,
            )
            await _record_endpoint_protocol_switch(
                session,
                profile=profile,
                protocol_error=protocol_error,
                completion=completion,
            )
            return completion
        except LLMRuntimeError as runtime_error:
            healed_adaptation = await _heal_stale_thinking_adaptation(
                session,
                profile,
                active_adaptation,
                runtime_error,
            )
            if healed_adaptation is not None:
                try:
                    completion = await _request_completion_endpoint(
                        profile,
                        payload,
                        endpoint_kind=healed_adaptation.endpoint_kind,
                        extra_body=healed_adaptation.thinking_extra_body,
                        allow_empty_content=allow_empty_content,
                    )
                except LLMRuntimeError as retry_error:
                    retry_error.attempted_urls = _merge_attempted_urls(
                        active_adaptation.endpoint_attempted_urls,
                        healed_adaptation.endpoint_attempted_urls,
                        retry_error.attempted_urls,
                    )
                    raise
                completion.attempted_urls = _merge_attempted_urls(
                    active_adaptation.endpoint_attempted_urls,
                    healed_adaptation.endpoint_attempted_urls,
                    completion.attempted_urls,
                )
                return completion
            runtime_error.attempted_urls = [
                *active_adaptation.endpoint_attempted_urls,
                *runtime_error.attempted_urls,
            ]
            raise

    if adaptation is not None:
        try:
            return await _request_completion_endpoint(
                profile,
                payload,
                endpoint_kind=adaptation.endpoint_kind,
                extra_body=adaptation.thinking_extra_body,
                allow_empty_content=allow_empty_content,
            )
        except LLMEndpointProtocolError:
            from .adaptation.endpoint import endpoint_candidates

            fallback_kind = endpoint_candidates(adaptation.endpoint_kind)[0]
            return await _request_completion_endpoint(
                profile,
                payload,
                endpoint_kind=fallback_kind,
                extra_body=adaptation.thinking_extra_body,
                allow_empty_content=allow_empty_content,
            )

    chat_error: LLMEndpointProtocolError | None = None
    try:
        return await _request_completion_endpoint(
            profile,
            payload,
            endpoint_kind="chat_completions",
            extra_body=extra_body,
            allow_empty_content=allow_empty_content,
        )
    except LLMEndpointProtocolError as exc:
        chat_error = exc

    assert chat_error is not None

    try:
        completion = await _request_completion_endpoint(
            profile,
            payload,
            endpoint_kind="responses",
            extra_body=extra_body,
            allow_empty_content=allow_empty_content,
        )
    except LLMRuntimeError as responses_error:
        responses_error.attempted_urls = [
            *chat_error.attempted_urls,
            *responses_error.attempted_urls,
        ]
        responses_error.args = (f"{responses_error}；此前已尝试：{chat_error}",)
        raise

    completion.attempted_urls = [*chat_error.attempted_urls, *completion.attempted_urls]
    return completion


async def request_structured_completion(
    profile: LLMProfile,
    payload: dict[str, object],
    result_model: type[StructuredResultT],
    *,
    extra_body: dict[str, object] | None = None,
    session: "AsyncSession | None" = None,
    adaptation: LLMRuntimeAdaptation | None = None,
    validation_error_message: str | None = None,
) -> tuple[ChatCompletionResult, StructuredResultT, str]:
    """Request and validate one typed JSON result without repair calls."""

    active_adaptation = adaptation
    mode = "prompt_only"
    request_payload = payload
    if session is not None:
        active_adaptation = active_adaptation or await ensure_llm_runtime_adaptation(
            session,
            profile,
        )
        from .adaptation.structured_output import (
            ensure_structured_output_adaptation,
            invalidate_structured_output_adaptation,
            is_structured_output_protocol_rejection,
        )

        mode = await ensure_structured_output_adaptation(
            session,
            profile,
            endpoint_kind=active_adaptation.endpoint_kind,
            thinking_extra_body=active_adaptation.thinking_extra_body,
        )
        if mode != "prompt_only":
            schema_name = re.sub(r"[^a-zA-Z0-9_-]", "_", result_model.__name__).lower()
            request_payload = with_structured_output(
                payload,
                mode=mode,
                schema=(
                    _prepare_strict_json_schema(result_model.model_json_schema())
                    if mode == "json_schema_strict"
                    else None
                ),
                schema_name=schema_name,
            )
        try:
            completion = await request_chat_completion(
                profile,
                request_payload,
                session=session,
                adaptation=active_adaptation,
            )
        except LLMRuntimeError as error:
            if mode != "prompt_only" and is_structured_output_protocol_rejection(error):
                await invalidate_structured_output_adaptation(
                    session,
                    api_base_url=resolve_base_url(profile.api_base_url),
                    model_name=profile.model_name,
                    endpoint_kind=active_adaptation.endpoint_kind,
                    expected_mode=mode,
                )
            raise
    else:
        completion = await request_chat_completion(
            profile,
            request_payload,
            extra_body=extra_body,
            adaptation=active_adaptation,
        )

    try:
        validation_context = {"structured_output_mode": mode}
        if mode == "prompt_only":
            result = parse_structured_result(
                completion.content,
                result_model,
                context=validation_context,
            )
        else:
            data = json.loads(completion.content)
            result = result_model.model_validate(data, context=validation_context)
    except LLMRuntimeError as error:
        raise LLMRuntimeError(
            validation_error_message or str(error),
            request_url=completion.request_url,
            attempted_urls=completion.attempted_urls,
            endpoint_kind=completion.endpoint_kind,
            status_code=completion.status_code,
            duration_ms=completion.duration_ms,
            usage=completion.usage,
            raw_content=completion.content,
        ) from error
    except (json.JSONDecodeError, ValidationError) as error:
        if (
            session is not None
            and mode == "json_schema_strict"
            and active_adaptation is not None
        ):
            from .adaptation.structured_output import (
                invalidate_structured_output_adaptation,
            )

            await invalidate_structured_output_adaptation(
                session,
                api_base_url=resolve_base_url(profile.api_base_url),
                model_name=profile.model_name,
                endpoint_kind=active_adaptation.endpoint_kind,
                expected_mode="json_schema_strict",
            )
        raise LLMRuntimeError(
            validation_error_message or f"模型返回的 JSON 结构无效: {error}",
            request_url=completion.request_url,
            attempted_urls=completion.attempted_urls,
            endpoint_kind=completion.endpoint_kind,
            status_code=completion.status_code,
            duration_ms=completion.duration_ms,
            usage=completion.usage,
            raw_content=completion.content,
        ) from error
    return completion, result, mode


def extract_json_object(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMRuntimeError("模型未返回 JSON 对象")
    return text[start : end + 1]


def parse_structured_result(
    raw_text: str,
    result_model: type[StructuredResultT],
    *,
    context: dict[str, object] | None = None,
) -> StructuredResultT:
    try:
        data = json.loads(extract_json_object(raw_text))
        result = result_model.model_validate(data, context=context)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise LLMRuntimeError(f"模型返回的 JSON 结构无效: {exc}") from exc
    if result_model is DraftGenerationResult:
        return _normalize_draft_generation_result(result)
    return result


def _draft_generation_wire_to_result(
    result: DraftGenerationWireResult,
) -> DraftGenerationResult:
    blocks: list[dict[str, object]] = []
    for block in result.blocks:
        if block.type == "paragraph":
            if len(block.items) != 1:
                raise LLMRuntimeError("模型返回的 paragraph 必须恰好包含一个 items 项")
            blocks.append(
                {
                    "type": "paragraph",
                    "children": _draft_body_item_to_nodes(block.items[0]),
                }
            )
            continue
        if not block.items:
            raise LLMRuntimeError("模型返回的列表正文不能为空")
        blocks.append(
            {
                "type": block.type,
                "items": [_draft_body_item_to_nodes(item) for item in block.items],
            }
        )

    return _normalize_draft_generation_result(
        DraftGenerationResult(
            subject=result.subject,
            rich_body={"type": "doc", "blocks": blocks},
        )
    )


def _draft_body_item_to_nodes(item: DraftBodyItemWire) -> list[dict[str, object]]:
    if not item.runs:
        raise LLMRuntimeError("模型返回的富文本 items.runs 不能为空")

    nodes: list[dict[str, object]] = []
    for run in item.runs:
        node: dict[str, object] = {"type": "text", "text": run.text}
        href = run.href.strip()
        if href:
            if not href.startswith(("http://", "https://", "mailto:")):
                raise LLMRuntimeError("模型返回了不支持的富文本链接协议")
            node = {"type": "link", "href": href, "children": [node]}
        if run.emphasis:
            node = {"type": "emphasis", "children": [node]}
        if run.strong:
            node = {"type": "strong", "children": [node]}
        nodes.append(node)
        if run.line_break_after:
            nodes.append({"type": "line_break"})
    return nodes


def _normalize_draft_generation_result(
    result: DraftGenerationResult,
) -> DraftGenerationResult:
    result.subject = _normalize_text_field(result.subject, "subject")
    if result.rich_body is not None:
        rendered = render_rich_text_document(result.rich_body)
    elif result.body_html:
        rendered = normalize_email_html(result.body_html)
    elif result.body_text:
        rendered = text_to_email_html(result.body_text)
    else:
        raise LLMRuntimeError("模型返回的富文本正文为空")
    result.body_text = rendered.text
    result.body_html = rendered.html
    return result


def _normalize_text_field(value: str, field_name: str) -> str:
    cleaned = " ".join(value.split()) if field_name == "subject" else value.strip()
    if not cleaned:
        raise LLMRuntimeError(f"模型返回的 {field_name} 为空")
    return cleaned


def _normalize_string_list(values: list[str], max_items: int) -> list[str]:
    normalized: list[str] = []
    for value in values:
        cleaned = str(value).strip().strip("-•")
        cleaned = re.sub(r"\s+", " ", cleaned)
        if not cleaned or cleaned in normalized:
            continue
        normalized.append(cleaned)
        if len(normalized) >= max_items:
            break
    return normalized


def estimate_text_tokens(text: str) -> int:
    if not text.strip():
        return 0
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_count = len(re.findall(r"[A-Za-z0-9_]", text))
    other_count = max(len(text) - cjk_count - ascii_count, 0)
    return max(cjk_count + ceil(ascii_count / 4) + ceil(other_count / 3), 1)


__all__ = [
    "sanitize_llm_url",
    "DEFAULT_BASE_URL",
    "DEFAULT_LLM_MAX_TOKENS",
    "DEFAULT_LLM_TEMPERATURE",
    "STEPFUN_PROBE_MAX_TOKENS",
    "STRUCTURED_OUTPUT_CONTROL_KEY",
    "SYSTEM_DRAFT_PROMPT",
    "SYSTEM_DRAFT_REWRITE_PROMPT",
    "SYSTEM_MATCH_ONLY_PROMPT",
    "ChatCompletionResult",
    "ChatCompletionUsage",
    "DraftBodyBlockWire",
    "DraftBodyItemWire",
    "DraftBodyRunWire",
    "DraftGenerationResult",
    "DraftGenerationWireResult",
    "DraftRewritePreferences",
    "DraftRewritePromptParts",
    "DraftRewriteResult",
    "DraftRewriteSegmentReplacement",
    "DraftTokenEstimate",
    "GeneratedDraftContent",
    "GeneratedMatchEvaluation",
    "LLMEmptyContentError",
    "LLMEndpointProtocolError",
    "LLMModelCatalogResult",
    "LLMProbeResult",
    "LLMRuntimeAdaptation",
    "LLMRuntimeError",
    "MatchEvaluationResult",
    "MatchEvaluationWireResult",
    "MatchPromptParts",
    "StructuredResultT",
    "build_chat_completions_payload",
    "build_draft_prompt",
    "build_draft_rewrite_constraints",
    "build_draft_rewrite_preferences",
    "build_draft_rewrite_prompt",
    "build_draft_rewrite_prompt_parts",
    "build_endpoint_url",
    "build_match_prompt",
    "build_match_prompt_parts",
    "build_responses_payload",
    "compute_duration_ms",
    "ensure_llm_runtime_adaptation",
    "estimate_draft_content_tokens",
    "estimate_text_tokens",
    "extract_chat_completion_content",
    "extract_json_object",
    "extract_model_ids",
    "extract_responses_content",
    "fetch_llm_profile_models",
    "format_http_error",
    "format_llm_client_initialization_error",
    "format_llm_runtime_error_for_user",
    "generate_draft_content",
    "generate_match_evaluation",
    "is_deepseek_profile",
    "is_stepfun_profile",
    "parse_completion_usage",
    "parse_structured_result",
    "probe_llm_profile",
    "probe_max_tokens_for_profile",
    "request_chat_completion",
    "request_structured_completion",
    "resolve_base_url",
    "resolve_template_text",
    "with_structured_output",
]
