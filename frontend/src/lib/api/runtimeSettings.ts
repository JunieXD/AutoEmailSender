import { apiFetch } from "@/lib/api/client";

export type DraftRewriteIntensity = "light" | "moderate" | "strong";
export type DraftRewriteTone = "polite" | "professional" | "friendly";
export type DraftRewriteFormality = "natural" | "balanced" | "formal";
export type DraftRewriteLength = "shorter" | "default" | "more_detailed";
export type DraftRewriteSpecificity = "concise" | "balanced" | "detailed";
export type DraftTemplatePreservation = "structure_first" | "balanced" | "content_first";

export interface RuntimeSettingsDTO {
  match_analysis_job_worker_count: number;
  match_analysis_job_item_concurrency: number;
  match_analysis_job_interval_seconds: number;
  crawler_worker_count: number;
  crawler_profile_enrichment_concurrency: number;
  crawler_host_concurrency: number;
  draft_max_tokens: number;
  batch_draft_generation_concurrency: number;
  draft_rewrite_intensity: DraftRewriteIntensity;
  draft_rewrite_tone: DraftRewriteTone;
  draft_rewrite_formality: DraftRewriteFormality;
  draft_rewrite_length: DraftRewriteLength;
  draft_rewrite_specificity: DraftRewriteSpecificity;
  draft_template_preservation: DraftTemplatePreservation;
  draft_custom_instruction: string;
  updated_at: string;
}

export type RuntimeSettingsUpdateDTO = Omit<RuntimeSettingsDTO, "updated_at">;

export const defaultDraftRewritePreferences = {
  draft_rewrite_intensity: "moderate",
  draft_rewrite_tone: "polite",
  draft_rewrite_formality: "balanced",
  draft_rewrite_length: "default",
  draft_rewrite_specificity: "balanced",
  draft_template_preservation: "structure_first",
} satisfies Pick<
  RuntimeSettingsDTO,
  | "draft_rewrite_intensity"
  | "draft_rewrite_tone"
  | "draft_rewrite_formality"
  | "draft_rewrite_length"
  | "draft_rewrite_specificity"
  | "draft_template_preservation"
>;

export const getRuntimeSettings = () =>
  apiFetch<RuntimeSettingsDTO>("/api/runtime-settings");

export const updateRuntimeSettings = (payload: RuntimeSettingsUpdateDTO) =>
  apiFetch<RuntimeSettingsDTO>("/api/runtime-settings", {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
