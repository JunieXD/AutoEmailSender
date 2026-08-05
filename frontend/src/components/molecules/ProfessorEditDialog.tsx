import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ExternalLink, Loader2 } from "lucide-react";
import { useNotification } from "@/context/NotificationContext";
import { useDismissableLayerClick } from "@/lib/useDismissableLayerClick";
import { useDocumentScrollLock } from "@/lib/useDocumentScrollLock";
import {
  normalizeExternalHttpUrl,
  openExternalHttpUrl,
} from "@/lib/externalUrls";
import { updateProfessor } from "@/lib/api/professorsApi";
import type {
  ProfessorDTO,
  ProfessorManagementItemDTO,
  ProfessorUpsertPayloadDTO,
} from "@/types";

type ProfessorEditFormState = {
  name: string;
  email: string;
  title: string;
  university: string;
  school: string;
  department: string;
  research_direction: string;
  recent_papers_text: string;
  personal_note: string;
  profile_url: string;
  source_url: string;
};

export type ProfessorEditDialogProps = {
  open: boolean;
  professor: ProfessorDTO | null;
  loading?: boolean;
  onClose: () => void;
  onSaved?: (professor: ProfessorManagementItemDTO) => void | Promise<void>;
};

const emptyForm = (): ProfessorEditFormState => ({
  name: "",
  email: "",
  title: "",
  university: "",
  school: "",
  department: "",
  research_direction: "",
  recent_papers_text: "",
  personal_note: "",
  profile_url: "",
  source_url: "",
});

const toFormState = (professor: ProfessorDTO): ProfessorEditFormState => ({
  name: professor.name,
  email: professor.email ?? "",
  title: professor.title ?? "",
  university: professor.university ?? "",
  school: professor.school ?? "",
  department: professor.department ?? "",
  research_direction: professor.research_direction ?? "",
  recent_papers_text: (professor.recent_papers ?? []).join("\n"),
  personal_note: professor.personal_note ?? "",
  profile_url: professor.profile_url ?? "",
  source_url: professor.source_url ?? "",
});

const toPayload = (
  form: ProfessorEditFormState,
  professor: ProfessorDTO,
): ProfessorUpsertPayloadDTO => ({
  name: form.name.trim(),
  email: form.email.trim(),
  title: form.title.trim() || null,
  university: form.university.trim() || null,
  school: form.school.trim() || null,
  department: form.department.trim() || null,
  research_direction: form.research_direction.trim() || null,
  recent_papers: form.recent_papers_text
    .split(/\r?\n/)
    .map((paper) => paper.trim())
    .filter(Boolean),
  personal_note: form.personal_note.trim() || null,
  profile_url: form.profile_url.trim() || null,
  source_url: form.source_url.trim() || null,
  tag_ids: (professor.tags ?? []).map((tag) => tag.id),
});

const fieldLabelClassName =
  "mb-2 inline-flex items-center gap-1 text-sm font-medium text-stone-800";
const inputClassName =
  "w-full rounded-2xl border border-stone-200 bg-white px-3 py-2.5 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15";
const urlInputClassName =
  "w-full rounded-2xl border border-stone-200 bg-white py-2.5 pl-3 pr-11 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15";

const fieldLabel = (label: string, required = false) => (
  <span className={fieldLabelClassName}>
    {required ? <span className="text-base leading-none text-red-500">*</span> : null}
    <span>{label}</span>
  </span>
);

const UrlInput = ({
  id,
  label,
  value,
  placeholder,
  openLabel,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  placeholder: string;
  openLabel: string;
  onChange: (value: string) => void;
}) => {
  const openableUrl = normalizeExternalHttpUrl(value);
  return (
    <div className="block">
      <label htmlFor={id}>{fieldLabel(label)}</label>
      <div className="relative">
        <input
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className={urlInputClassName}
          placeholder={placeholder}
        />
        <button
          type="button"
          aria-label={openLabel}
          title={openLabel}
          disabled={!openableUrl}
          onClick={() => {
            if (openableUrl) {
              openExternalHttpUrl(openableUrl);
            }
          }}
          className="absolute right-1.5 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-xl border border-stone-200 bg-stone-50 text-stone-500 transition hover:border-primary/40 hover:bg-white hover:text-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-45"
        >
          <ExternalLink className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
};

const DialogShell = ({
  open,
  title,
  description,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  description: string;
  onClose: () => void;
  children: ReactNode;
}) => {
  const layer = useDismissableLayerClick(onClose);
  useDocumentScrollLock(open);

  useEffect(() => {
    if (!open) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  if (!open) {
    return null;
  }

  return (
    <div
      role="dialog"
      aria-label={title}
      aria-modal="true"
      className="fixed inset-0 z-[90] flex items-center justify-center bg-stone-950/35 p-4 backdrop-blur-md"
      onClick={layer.onBackdropClick}
      onMouseDown={layer.onBackdropMouseDown}
    >
      <div
        className="relative w-full max-w-3xl overflow-hidden rounded-[32px] border border-stone-200/80 bg-[linear-gradient(180deg,rgba(255,252,246,0.98),rgba(255,245,233,0.96))] shadow-[0_34px_90px_-32px_rgba(41,37,36,0.5)]"
        onClick={layer.onContentClick}
        onMouseDown={layer.onContentMouseDown}
      >
        <div className="relative max-h-[85vh] overflow-y-auto overscroll-contain px-6 py-6">
          <h2 className="break-words text-2xl font-semibold tracking-[0.01em] text-stone-900">
            {title}
          </h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-stone-600">
            {description}
          </p>
          {children}
        </div>
      </div>
    </div>
  );
};

export const ProfessorEditDialog = ({
  open,
  professor,
  loading = false,
  onClose,
  onSaved,
}: ProfessorEditDialogProps) => {
  const { notifyError, notifySuccess } = useNotification();
  const [form, setForm] = useState<ProfessorEditFormState>(emptyForm());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (professor) {
      setForm(toFormState(professor));
    } else if (!open) {
      setForm(emptyForm());
    }
  }, [open, professor]);

  const handleClose = () => {
    if (!saving) {
      onClose();
    }
  };

  const handleSave = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!professor || saving) {
      return;
    }
    const payload = toPayload(form, professor);
    if (!payload.name) {
      notifyError("无法保存导师资料", "导师姓名不能为空。");
      return;
    }
    if (!payload.email) {
      notifyError("无法保存导师资料", "导师邮箱不能为空。");
      return;
    }

    setSaving(true);
    try {
      const updated = await updateProfessor(professor.id, payload);
      notifySuccess("导师资料已保存", `已更新“${updated.name}”的资料。`);
      onClose();
      try {
        await onSaved?.(updated);
      } catch {
        // The parent owns refresh errors; a successful profile save remains committed.
      }
    } catch (error) {
      notifyError(
        "保存导师资料失败",
        error instanceof Error ? error.message : "保存导师资料失败",
      );
    } finally {
      setSaving(false);
    }
  };

  const title = professor ? `补充导师资料：${professor.name}` : "补充导师资料";

  return (
    <DialogShell
      open={open}
      title={title}
      description="直接在当前任务页补充导师资料。保存后会回到批量任务，不需要重新打开导师管理页。"
      onClose={handleClose}
    >
      {loading || !professor ? (
        <div
          aria-label="导师资料加载中"
          className="flex min-h-56 items-center justify-center gap-2 text-sm text-stone-500"
        >
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载导师资料…
        </div>
      ) : (
        <form onSubmit={(event) => void handleSave(event)}>
          <div className="mt-6 grid gap-4 md:grid-cols-2">
            <label className="block">
              {fieldLabel("姓名", true)}
              <input
                value={form.name}
                onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                className={inputClassName}
                placeholder="示例：张明远"
              />
            </label>
            <label className="block">
              {fieldLabel("邮箱", true)}
              <input
                value={form.email}
                onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))}
                className={inputClassName}
                placeholder="示例：faculty@example.edu"
              />
            </label>
            <label className="block">
              {fieldLabel("职称")}
              <input
                value={form.title}
                onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))}
                className={inputClassName}
                placeholder="示例：Associate Professor"
              />
            </label>
            <label className="block">
              {fieldLabel("学校")}
              <input
                value={form.university}
                onChange={(event) => setForm((current) => ({ ...current, university: event.target.value }))}
                className={inputClassName}
                placeholder="示例：Tsinghua University"
              />
            </label>
            <label className="block">
              {fieldLabel("学院")}
              <input
                value={form.school}
                onChange={(event) => setForm((current) => ({ ...current, school: event.target.value }))}
                className={inputClassName}
                placeholder="示例：School of Computer Science"
              />
            </label>
            <label className="block">
              {fieldLabel("系所")}
              <input
                value={form.department}
                onChange={(event) => setForm((current) => ({ ...current, department: event.target.value }))}
                className={inputClassName}
                placeholder="示例：Department of AI"
              />
            </label>
            <label className="block md:col-span-2">
              {fieldLabel("研究方向")}
              <textarea
                aria-label="研究方向"
                value={form.research_direction}
                onChange={(event) => setForm((current) => ({ ...current, research_direction: event.target.value }))}
                className="min-h-28 w-full rounded-2xl border border-stone-200 bg-white px-3 py-3 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
                placeholder="示例：Large Language Models, Information Extraction, NLP"
              />
            </label>
            <label className="block md:col-span-2">
              {fieldLabel("近期论文")}
              <textarea
                value={form.recent_papers_text}
                onChange={(event) => setForm((current) => ({ ...current, recent_papers_text: event.target.value }))}
                className="min-h-32 w-full rounded-2xl border border-stone-200 bg-white px-3 py-3 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
                placeholder={"一行一篇，例如：\nScaling Agents with...\nReasoning for Scientific Discovery..."}
              />
            </label>
            <label className="block md:col-span-2">
              {fieldLabel("个人备注")}
              <textarea
                aria-label="个人备注"
                value={form.personal_note}
                onChange={(event) => setForm((current) => ({ ...current, personal_note: event.target.value }))}
                maxLength={10000}
                className="min-h-28 w-full rounded-2xl border border-stone-200 bg-white px-3 py-3 text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
                placeholder="只对自己可见的沟通偏好、判断依据或跟进提醒。"
              />
            </label>
            <UrlInput
              id="task-professor-profile-url"
              label="高校官网详情页"
              value={form.profile_url}
              placeholder="示例：https://example.edu/faculty/zhang"
              openLabel="打开高校官网详情页"
              onChange={(value) => setForm((current) => ({ ...current, profile_url: value }))}
            />
            <UrlInput
              id="task-professor-source-url"
              label="发现来源页"
              value={form.source_url}
              placeholder="示例：https://example.edu/faculty-directory"
              openLabel="打开发现来源页"
              onChange={(value) => setForm((current) => ({ ...current, source_url: value }))}
            />
          </div>
          <div className="mt-6 flex flex-wrap justify-end gap-3">
            <button type="button" onClick={handleClose} className="ui-btn-secondary">
              取消
            </button>
            <button
              type="submit"
              disabled={saving}
              className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
            >
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              保存导师
            </button>
          </div>
        </form>
      )}
    </DialogShell>
  );
};
