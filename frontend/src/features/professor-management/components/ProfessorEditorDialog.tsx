import { ProfessorTagSelector } from "@/components/molecules/ProfessorTagSelector";
import { type ProfessorFormState } from "@/features/professor-management/model/professorManagementPage";
import type {
  ProfessorManagementItemDTO,
  ProfessorTagDTO,
  ProfessorTagPayloadDTO,
} from "@/types";
import { Bot, ExternalLink, Loader2, Share2 } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";
import type { TrackedSingleInformationEnrichment } from "../model/enrichmentTracking";
import {
  FieldLabel,
  ModalShell,
  UrlInputField,
  inputClassName,
} from "./formControls";
const activeInformationEnrichmentStatuses = new Set(["queued", "running"]);

type Props = {
  upsertModalOpen: boolean;
  editingProfessor: ProfessorManagementItemDTO | null;
  closeUpsertModal: () => void;
  handleSingleInformationEnrichment: () => Promise<void>;
  startingSingleInformationEnrichmentIds: Set<number>;
  singleInformationEnrichments: Record<
    number,
    TrackedSingleInformationEnrichment
  >;
  formState: ProfessorFormState;
  setFormState: Dispatch<SetStateAction<ProfessorFormState>>;
  professorTags: ProfessorTagDTO[];
  savingProfessor: boolean;
  handleCreateProfessorTag: (
    payload: ProfessorTagPayloadDTO,
  ) => Promise<ProfessorTagDTO | null>;
  handleDeleteProfessorTag: (tag: ProfessorTagDTO) => Promise<void>;
  handleContributeProfessor: () => Promise<void>;
  handleSaveProfessor: () => Promise<void>;
};

export function ProfessorEditorDialog({
  upsertModalOpen,
  editingProfessor,
  closeUpsertModal,
  handleSingleInformationEnrichment,
  startingSingleInformationEnrichmentIds,
  singleInformationEnrichments,
  formState,
  setFormState,
  professorTags,
  savingProfessor,
  handleCreateProfessorTag,
  handleDeleteProfessorTag,
  handleContributeProfessor,
  handleSaveProfessor,
}: Props) {
  return (
    <ModalShell
      open={upsertModalOpen}
      title={
        editingProfessor ? `编辑导师：${editingProfessor.name}` : "新增导师"
      }
      description="保存后可立即用于筛选和创建任务。"
      onClose={closeUpsertModal}
      headerAction={
        editingProfessor ? (
          <button
            type="button"
            onClick={() => void handleSingleInformationEnrichment()}
            disabled={
              startingSingleInformationEnrichmentIds.has(editingProfessor.id) ||
              activeInformationEnrichmentStatuses.has(
                singleInformationEnrichments[editingProfessor.id]?.job.status ??
                  "",
              )
            }
            className="ui-btn-secondary whitespace-nowrap disabled:cursor-not-allowed disabled:opacity-60"
          >
            {startingSingleInformationEnrichmentIds.has(editingProfessor.id) ||
            activeInformationEnrichmentStatuses.has(
              singleInformationEnrichments[editingProfessor.id]?.job.status ??
                "",
            ) ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Bot className="h-4 w-4" />
            )}
            智能补全
          </button>
        ) : null
      }
    >
      <div className="mt-6 grid gap-4 md:grid-cols-2">
        <label className="block">
          {<FieldLabel label={"姓名"} required={true} />}
          <input
            value={formState.name}
            onChange={(event) =>
              setFormState((previous) => ({
                ...previous,
                name: event.target.value,
              }))
            }
            className={inputClassName}
            placeholder="示例：张明远"
          />
        </label>
        <label className="block">
          {<FieldLabel label={"邮箱"} required={true} />}
          <input
            value={formState.email}
            onChange={(event) =>
              setFormState((previous) => ({
                ...previous,
                email: event.target.value,
              }))
            }
            className={inputClassName}
            placeholder="示例：faculty@example.edu"
          />
        </label>
        <label className="block">
          {<FieldLabel label={"职称"} />}
          <input
            value={formState.title}
            onChange={(event) =>
              setFormState((previous) => ({
                ...previous,
                title: event.target.value,
              }))
            }
            className={inputClassName}
            placeholder="示例：Associate Professor"
          />
        </label>
        <label className="block">
          {<FieldLabel label={"学校"} />}
          <input
            value={formState.university}
            onChange={(event) =>
              setFormState((previous) => ({
                ...previous,
                university: event.target.value,
              }))
            }
            className={inputClassName}
            placeholder="示例：Tsinghua University"
          />
        </label>
        <label className="block">
          {<FieldLabel label={"学院"} />}
          <input
            value={formState.school}
            onChange={(event) =>
              setFormState((previous) => ({
                ...previous,
                school: event.target.value,
              }))
            }
            className={inputClassName}
            placeholder="示例：School of Computer Science"
          />
        </label>
        <label className="block">
          {<FieldLabel label={"系所"} />}
          <input
            value={formState.department}
            onChange={(event) =>
              setFormState((previous) => ({
                ...previous,
                department: event.target.value,
              }))
            }
            className={inputClassName}
            placeholder="示例：Department of AI"
          />
        </label>
        <div className="md:col-span-2">
          <ProfessorTagSelector
            tags={professorTags}
            selectedTagIds={formState.tag_ids}
            disabled={savingProfessor}
            onChange={(tagIds) =>
              setFormState((previous) => ({
                ...previous,
                tag_ids: tagIds,
              }))
            }
            onCreateTag={(payload) => void handleCreateProfessorTag(payload)}
            onDeleteTag={(tag) => void handleDeleteProfessorTag(tag)}
          />
        </div>
        <label className="block md:col-span-2">
          {<FieldLabel label={"研究方向"} />}
          <textarea
            value={formState.research_direction}
            onChange={(event) =>
              setFormState((previous) => ({
                ...previous,
                research_direction: event.target.value,
              }))
            }
            className="min-h-28 w-full rounded-2xl border border-stone-200 bg-white px-3 py-3 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
            placeholder="示例：Large Language Models, Information Extraction, NLP"
          />
        </label>
        <label className="block md:col-span-2">
          {<FieldLabel label={"近期论文"} />}
          <textarea
            value={formState.recent_papers_text}
            onChange={(event) =>
              setFormState((previous) => ({
                ...previous,
                recent_papers_text: event.target.value,
              }))
            }
            className="min-h-32 w-full rounded-2xl border border-stone-200 bg-white px-3 py-3 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
            placeholder={
              "一行一篇，例如：\nScaling Agents with…\nReasoning for Scientific Discovery…"
            }
          />
        </label>
        <label className="block md:col-span-2">
          {<FieldLabel label={"个人备注"} />}
          <textarea
            aria-label="个人备注"
            value={formState.personal_note}
            onChange={(event) =>
              setFormState((previous) => ({
                ...previous,
                personal_note: event.target.value,
              }))
            }
            maxLength={10000}
            className="min-h-28 w-full rounded-2xl border border-stone-200 bg-white px-3 py-3 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
            placeholder="只对自己可见的沟通偏好、判断依据或跟进提醒。"
          />
        </label>
        <UrlInputField
          id="professor-profile-url"
          label="导师主页"
          value={formState.profile_url}
          placeholder="示例：https://example.edu/faculty/zhang"
          openLabel="打开导师主页"
          onChange={(value) =>
            setFormState((previous) => ({
              ...previous,
              profile_url: value,
            }))
          }
        />
        <UrlInputField
          id="professor-source-url"
          label="发现来源页"
          value={formState.source_url}
          placeholder="示例：https://example.edu/faculty-directory"
          openLabel="打开发现来源页"
          onChange={(value) =>
            setFormState((previous) => ({
              ...previous,
              source_url: value,
            }))
          }
        />
      </div>
      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-3">
          {editingProfessor ? (
            <button
              type="button"
              onClick={() => void handleContributeProfessor()}
              className="ui-btn-secondary"
            >
              <Share2 className="h-4 w-4" />
              贡献到社区
              <ExternalLink className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            onClick={closeUpsertModal}
            className="ui-btn-secondary"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void handleSaveProfessor()}
            disabled={savingProfessor}
            className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
          >
            {savingProfessor ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : null}
            保存导师
          </button>
        </div>
      </div>
    </ModalShell>
  );
}
