from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


DraftRewriteIntensity = Literal["light", "moderate", "strong"]
DraftRewriteTone = Literal["polite", "professional", "friendly"]
DraftRewriteFormality = Literal["natural", "balanced", "formal"]
DraftRewriteLength = Literal["shorter", "default", "more_detailed"]
DraftRewriteSpecificity = Literal["concise", "balanced", "detailed"]
DraftTemplatePreservation = Literal["structure_first", "balanced", "content_first"]


class RuntimeSettingsRead(BaseModel):
    match_analysis_job_worker_count: int
    match_analysis_job_item_concurrency: int
    match_analysis_job_interval_seconds: int
    crawler_worker_count: int
    crawler_profile_enrichment_concurrency: int
    crawler_host_concurrency: int
    draft_max_tokens: int
    batch_draft_generation_concurrency: int
    draft_rewrite_intensity: DraftRewriteIntensity
    draft_rewrite_tone: DraftRewriteTone
    draft_rewrite_formality: DraftRewriteFormality
    draft_rewrite_length: DraftRewriteLength
    draft_rewrite_specificity: DraftRewriteSpecificity
    draft_template_preservation: DraftTemplatePreservation
    draft_custom_instruction: str
    updated_at: datetime


class RuntimeSettingsUpdate(BaseModel):
    match_analysis_job_worker_count: int = Field(ge=1, le=8)
    match_analysis_job_item_concurrency: int = Field(ge=1, le=20)
    match_analysis_job_interval_seconds: int = Field(ge=1, le=300)
    crawler_worker_count: int = Field(ge=1, le=8)
    crawler_profile_enrichment_concurrency: int = Field(ge=1, le=20)
    crawler_host_concurrency: int = Field(ge=1, le=8)
    draft_max_tokens: int = Field(ge=256, le=32000)
    batch_draft_generation_concurrency: int = Field(ge=1, le=20)
    draft_rewrite_intensity: DraftRewriteIntensity
    draft_rewrite_tone: DraftRewriteTone
    draft_rewrite_formality: DraftRewriteFormality
    draft_rewrite_length: DraftRewriteLength
    draft_rewrite_specificity: DraftRewriteSpecificity
    draft_template_preservation: DraftTemplatePreservation
    draft_custom_instruction: str = Field(max_length=2000)
