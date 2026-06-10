import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useRef } from 'react';
import { Loader2 } from 'lucide-react';
import { NativeSelectField } from '@/components/atoms/NativeSelectField';
import { EmailTemplateEditor } from '@/components/molecules/EmailTemplateEditor';
import { SubjectTemplateInput } from '@/components/molecules/SubjectTemplateInput';
import { TaskDateSelector } from '@/components/molecules/TaskDateSelector';
import { useNotification } from '@/context/NotificationContext';
import { safeRecordUserAction } from '@/lib/diagnosticUserActions';
import { createBatchTask } from '@/lib/api/batchTasksApi';
import {
  clearBatchResendPrefillContext,
  readBatchResendPrefillContext,
} from '@/features/batch-tasks/client/batchTaskResendPrefill';
import { listProfessors } from '@/lib/api/professorsApi';
import { getPageItems, getTotalPages, PAGE_SIZE } from '@/lib/pagination';
import { textToEmailHtml } from '@/lib/richEmail';
import { useSelectionContext } from '@/context/SelectionContext';
import { getTaskModeCopy } from '@/features/create-task/client/taskCopy';
import { buildBatchCreateConfirmDescription } from '@/features/create-task/client/batchCreateConfirmDescription';
import {
  hasFutureScheduleWindow,
  normalizeScheduledDates,
} from '@/features/create-task/client/scheduleDates';
import { useConfirmDialog } from '@/lib/useConfirmDialog';
import {
  MATERIAL_TYPE_LABELS,
  type IdentityMaterialDTO,
  type OutreachGenerationMode,
  type ProfessorDashboardItemDTO,
} from '@/types';

const SESSION_KEY = 'selected_professor_ids';
const PRIMARY_MATERIAL_EXTENSIONS = ['.pdf', '.doc', '.docx', '.txt', '.md'];
const TARGET_MENTORS_PAGE_SIZE = PAGE_SIZE;

const readSelectedProfessorIds = () => {
  try {
    const raw = window.sessionStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as number[]) : [];
  } catch {
    return [];
  }
};

const isPrimaryMaterialCandidate = (material: IdentityMaterialDTO) => {
  const filename = material.original_filename.toLowerCase();
  return PRIMARY_MATERIAL_EXTENSIONS.some((suffix) => filename.endsWith(suffix));
};

const MODE_OPTIONS: Array<{
  value: OutreachGenerationMode;
  title: string;
  description: string;
}> = (['llm', 'template'] as const).map((value) => ({
  value,
  ...getTaskModeCopy(value),
}));

export const CreateTaskPage = () => {
  const navigate = useNavigate();
  const { notifyError, notifyFormErrors } = useNotification();
  const { confirm, dialog: confirmDialog } = useConfirmDialog();
  const { selectedIdentityId, selectedLlmProfileId, selectedIdentity } = useSelectionContext();
  const [selectedProfessorIds] = useState<number[]>(readSelectedProfessorIds());
  const [resendPrefillContext] = useState(() => readBatchResendPrefillContext());
  const [professors, setProfessors] = useState<ProfessorDashboardItemDTO[]>([]);
  const [targetMentorsPage, setTargetMentorsPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [taskName, setTaskName] = useState(`批量任务 ${new Date().toLocaleDateString('zh-CN')}`);
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [bodyHtml, setBodyHtml] = useState('');
  const [taskMode, setTaskMode] = useState<OutreachGenerationMode>('llm');
  const [templateSubject, setTemplateSubject] = useState('');
  const [templateBodyText, setTemplateBodyText] = useState('');
  const [templateBodyHtml, setTemplateBodyHtml] = useState('');
  const [scheduleType, setScheduleType] = useState<'immediate' | 'scheduled'>('immediate');
  const [scheduledDates, setScheduledDates] = useState<string[]>([]);
  const [startTime, setStartTime] = useState('09:00');
  const [endTime, setEndTime] = useState('11:00');
  const [emailsPerWindow, setEmailsPerWindow] = useState('10');
  const [primaryMaterialId, setPrimaryMaterialId] = useState<number | null>(null);
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<number[]>([]);
  const loadedProfessorsKeyRef = useRef<string | null>(null);
  const activeProfessorsRequestKeyRef = useRef<string | null>(null);
  const latestProfessorsRequestIdRef = useRef(0);
  const isResendPrefillActive =
    resendPrefillContext !== null && resendPrefillContext.identityId === selectedIdentityId;
  const professorsRequestKey =
    selectedIdentityId && selectedProfessorIds.length > 0
      ? `${selectedIdentityId}:${selectedProfessorIds.join(',')}`
      : null;

  useEffect(() => {
    return () => {
      clearBatchResendPrefillContext();
    };
  }, []);

  useEffect(() => {
    const loadProfessors = async () => {
      if (!professorsRequestKey || !selectedIdentityId || selectedProfessorIds.length === 0) {
        latestProfessorsRequestIdRef.current += 1;
        activeProfessorsRequestKeyRef.current = null;
        loadedProfessorsKeyRef.current = null;
        setProfessors([]);
        setLoading(false);
        return;
      }
      const requestId = latestProfessorsRequestIdRef.current + 1;
      latestProfessorsRequestIdRef.current = requestId;
      activeProfessorsRequestKeyRef.current = professorsRequestKey;
      setLoading(true);
      try {
        const data = await listProfessors({
          identityId: selectedIdentityId,
          ids: selectedProfessorIds,
        });
        if (
          latestProfessorsRequestIdRef.current !== requestId ||
          activeProfessorsRequestKeyRef.current !== professorsRequestKey
        ) {
          return;
        }
        setProfessors(data);
        loadedProfessorsKeyRef.current = professorsRequestKey;
      } catch (loadError) {
        if (
          latestProfessorsRequestIdRef.current !== requestId ||
          activeProfessorsRequestKeyRef.current !== professorsRequestKey
        ) {
          return;
        }
        if (loadedProfessorsKeyRef.current !== professorsRequestKey) {
          setProfessors([]);
        }
        const message = loadError instanceof Error ? loadError.message : '加载已选导师失败';
        notifyError('加载已选导师失败', message);
      } finally {
        if (
          latestProfessorsRequestIdRef.current === requestId &&
          activeProfessorsRequestKeyRef.current === professorsRequestKey
        ) {
          setLoading(false);
        }
      }
    };

    void loadProfessors();
  }, [notifyError, professorsRequestKey, selectedIdentityId, selectedProfessorIds]);

  useEffect(() => {
    if (!selectedIdentity) {
      setPrimaryMaterialId(null);
      setSelectedMaterialIds([]);
      setTaskMode('llm');
      setSubject('');
      setBody('');
      setBodyHtml('');
      setTemplateSubject('');
      setTemplateBodyText('');
      setTemplateBodyHtml('');
      return;
    }
    const nextPrimaryMaterialId =
      selectedIdentity.current_primary_material &&
      isPrimaryMaterialCandidate(selectedIdentity.current_primary_material)
        ? selectedIdentity.current_primary_material.id
        : null;
    setPrimaryMaterialId(nextPrimaryMaterialId);
    setSelectedMaterialIds([]);
    setTaskMode(selectedIdentity.outreach_generation_mode ?? 'llm');
    setSubject(selectedIdentity.outreach_template_subject ?? '');
    const nextTemplateBodyText = selectedIdentity.outreach_template_body_text ?? '';
    const nextTemplateBodyHtml =
      selectedIdentity.outreach_template_body_html ?? (nextTemplateBodyText ? textToEmailHtml(nextTemplateBodyText) : '');
    setBody(nextTemplateBodyText);
    setBodyHtml(nextTemplateBodyHtml);
    setTemplateSubject(selectedIdentity.outreach_template_subject ?? '');
    setTemplateBodyText(nextTemplateBodyText);
    setTemplateBodyHtml(nextTemplateBodyHtml);

    if (isResendPrefillActive && resendPrefillContext) {
      setTaskName('重新发起 - ' + resendPrefillContext.sourceTaskName);
      const mode = resendPrefillContext.defaults.outreach_generation_mode ?? selectedIdentity.outreach_generation_mode ?? 'llm';
      const subjectValue = resendPrefillContext.defaults.outreach_template_subject ?? '';
      const bodyTextValue = resendPrefillContext.defaults.outreach_template_body_text ?? '';
      const bodyHtmlValue = resendPrefillContext.defaults.outreach_template_body_html ?? (bodyTextValue ? textToEmailHtml(bodyTextValue) : '');
      const materialIds = new Set(selectedIdentity.materials.map((material) => material.id));
      setTaskMode(mode);
      setSubject(subjectValue);
      setBody(bodyTextValue);
      setBodyHtml(bodyHtmlValue);
      setTemplateSubject(subjectValue);
      setTemplateBodyText(bodyTextValue);
      setTemplateBodyHtml(bodyHtmlValue);
      setPrimaryMaterialId(
        resendPrefillContext.defaults.primary_material_id !== null &&
          materialIds.has(resendPrefillContext.defaults.primary_material_id)
          ? resendPrefillContext.defaults.primary_material_id
          : null,
      );
      setSelectedMaterialIds(resendPrefillContext.defaults.selected_material_ids.filter((id) => materialIds.has(id)));
    } else if (resendPrefillContext) {
      clearBatchResendPrefillContext();
    }
  }, [isResendPrefillActive, resendPrefillContext, selectedIdentity]);

  const primaryMaterialOptions = useMemo(
    () => (selectedIdentity ? selectedIdentity.materials.filter(isPrimaryMaterialCandidate) : []),
    [selectedIdentity],
  );
  const targetMentorsTotalPages = getTotalPages(professors.length, TARGET_MENTORS_PAGE_SIZE);
  const visibleTargetMentors = useMemo(
    () => getPageItems(professors, targetMentorsPage, TARGET_MENTORS_PAGE_SIZE),
    [professors, targetMentorsPage],
  );

  useEffect(() => {
    setTargetMentorsPage(1);
  }, [professorsRequestKey]);

  useEffect(() => {
    setTargetMentorsPage((currentPage) => Math.min(currentPage, targetMentorsTotalPages));
  }, [targetMentorsTotalPages]);

  const templateReady = Boolean(templateBodyText.trim() || templateBodyHtml.trim());

  const handleSubmit = async () => {
    const validationErrors: string[] = [];
    const normalizedScheduledDates = normalizeScheduledDates(scheduledDates);

    if (!selectedIdentityId || !selectedLlmProfileId) {
      validationErrors.push('请先选择身份和模型');
    }
    if (!taskName.trim()) {
      validationErrors.push('任务名称不能为空');
    }
    if (professors.length === 0) {
      validationErrors.push('当前没有可执行的导师');
    }
    if (scheduleType === 'scheduled' && normalizedScheduledDates.length === 0) {
      validationErrors.push('定时发送请至少选择一个发送日期');
    }
    if (scheduleType === 'scheduled' && (!startTime || !endTime || !emailsPerWindow)) {
      validationErrors.push('定时发送需要填写发送时间窗口和窗口内发送数量');
    }
    if (
      scheduleType === 'scheduled' &&
      endTime &&
      normalizedScheduledDates.length > 0 &&
      !hasFutureScheduleWindow(normalizedScheduledDates, endTime)
    ) {
      validationErrors.push('当前定时发送窗口已全部过期，请重新选择发送日期或结束时间');
    }
    if (taskMode === 'template' && !templateReady) {
      validationErrors.push('直接套用模板需要填写纯文本正文或 HTML 正文');
    }
    if (taskMode === 'llm' && !body.trim()) {
      validationErrors.push('AI 辅助写信需要填写套磁信模板正文');
    }
    if (taskMode === 'llm' && primaryMaterialId === null) {
      validationErrors.push('AI 写信参考材料为必选项');
    }

    if (validationErrors.length > 0) {
      notifyFormErrors('请检查表单', validationErrors);
      return;
    }

    const identityId = selectedIdentityId;
    const llmProfileId = selectedLlmProfileId;

    if (!identityId || !llmProfileId) {
      return;
    }

    const confirmDescription = buildBatchCreateConfirmDescription(taskMode, scheduleType);

    const confirmed = await confirm({
      title: scheduleType === 'scheduled' ? '确认创建定时批量发送任务？' : '确认创建真实发送任务？',
      description: confirmDescription,
      confirmLabel: '继续创建',
      cancelLabel: '再检查一下',
      tone: 'danger',
    });
    if (!confirmed) {
      return;
    }

    const diagnosticData = {
      selectedCount: professors.length,
      identityId,
      llmProfileId,
      scheduleType,
    };
    safeRecordUserAction({
      eventName: 'tasks.batch_create_submitted',
      data: diagnosticData,
    });
    setSubmitting(true);
    try {
      const llmTemplateSubject = subject.trim() || null;
      const llmTemplateBodyText = body.trim() || null;
      const llmTemplateBodyHtml = bodyHtml.trim() || null;
      const taskTemplateSubject =
        taskMode === 'llm' ? llmTemplateSubject : templateSubject.trim() || null;
      const taskTemplateBodyText =
        taskMode === 'llm' ? llmTemplateBodyText : templateBodyText.trim() || null;
      const taskTemplateBodyHtml =
        taskMode === 'llm' ? llmTemplateBodyHtml : templateBodyHtml.trim() || null;
      const taskPrimaryMaterialId = taskMode === 'llm' ? primaryMaterialId : null;

      await createBatchTask({
        identity_id: identityId,
        llm_profile_id: llmProfileId,
        name: taskName.trim(),
        professor_ids: professors.map((item) => item.id),
        schedule_type: scheduleType,
        scheduled_dates: scheduleType === 'scheduled' ? normalizedScheduledDates : null,
        window_start_time: scheduleType === 'scheduled' ? startTime : null,
        window_end_time: scheduleType === 'scheduled' ? endTime : null,
        emails_per_window:
          scheduleType === 'scheduled' ? Number(emailsPerWindow || '0') || null : null,
        primary_material_id: taskPrimaryMaterialId,
        email_subject: llmTemplateSubject,
        email_body: llmTemplateBodyText,
        selected_material_ids: selectedMaterialIds.length ? selectedMaterialIds : null,
        outreach_generation_mode: taskMode,
        outreach_template_subject: taskTemplateSubject,
        outreach_template_body_text: taskTemplateBodyText,
        outreach_template_body_html: taskTemplateBodyHtml,
      });
      safeRecordUserAction({
        eventName: 'tasks.batch_create_succeeded',
        data: diagnosticData,
      });
      window.sessionStorage.removeItem(SESSION_KEY);
      clearBatchResendPrefillContext();
      navigate('/tasks');
    } catch (submitError) {
      const message = submitError instanceof Error ? submitError.message : '创建任务失败';
      safeRecordUserAction({
        eventName: 'tasks.batch_create_failed',
        data: diagnosticData,
        message,
        level: 'error',
      });
      notifyError('创建任务失败', message);
    } finally {
      setSubmitting(false);
    }
  };

  if (!selectedIdentityId || !selectedLlmProfileId || !selectedIdentity) {
    return (
      <main className="mx-auto max-w-4xl px-6 py-10">
        <div className="rounded-3xl border border-dashed border-stone-300 bg-[#fcfbf8] p-10 text-center">
          <h1 className="text-2xl font-semibold text-stone-900">选择身份和模型</h1>
          <p className="mt-3 text-sm text-stone-600">
            创建任务需要身份和模型。
          </p>
        </div>
      </main>
    );
  }

  if (selectedProfessorIds.length === 0) {
    return (
      <main className="mx-auto max-w-4xl px-6 py-10">
        <div className="rounded-3xl border border-dashed border-stone-300 bg-[#fcfbf8] p-10 text-center">
          <h1 className="text-2xl font-semibold text-stone-900">未选择导师</h1>
          <p className="mt-3 text-sm text-stone-600">
            返回首页选择目标导师。
          </p>
          <Link to="/" data-interactive="button" className="ui-btn-primary mt-6">
            返回首页
          </Link>
        </div>
      </main>
    );
  }

  return (
    <>
      <main className="mx-auto max-w-6xl px-6 py-8">
        <div className="rounded-3xl border border-stone-200 bg-[#fcfbf8] p-6 shadow-sm">
          <h1 className="text-3xl font-semibold text-stone-900">创建批量任务</h1>
          <p className="mt-2 text-sm text-stone-600">
            身份：{selectedIdentity.name} · 导师：{selectedProfessorIds.length} 位
          </p>
          {isResendPrefillActive && resendPrefillContext ? (
            <div className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900">
              已从「{resendPrefillContext.sourceTaskName}」带入 {resendPrefillContext.professorIds.length} 位老师、原身份、模板和材料。模型使用当前选择，发送时间需要重新设置；提交前可自行修改。
              {resendPrefillContext.warnings.map((warning) => (
                <span key={warning} className="mt-1 block text-xs text-amber-800">{warning}</span>
              ))}
            </div>
          ) : null}
        </div>

        {loading ? (
          <div className="mt-6 flex items-center justify-center gap-2 rounded-3xl border border-stone-200 bg-white px-6 py-14 text-sm text-stone-500 shadow-sm">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在加载已选导师...
          </div>
        ) : (
          <div className="mt-6 grid gap-6 lg:grid-cols-[1.45fr,0.85fr]">
          <section className="rounded-3xl border border-stone-200 bg-white p-6 shadow-sm">
            <div className="space-y-6">
              <label className="block">
                <div className="mb-2 text-sm font-medium text-stone-800">任务名称</div>
                <input
                  value={taskName}
                  onChange={(event) => setTaskName(event.target.value)}
                  className="form-input"
                />
              </label>

              <div className="rounded-[28px] border border-stone-200 bg-[linear-gradient(180deg,rgba(255,248,240,0.72),rgba(255,255,255,0.96))] p-4 shadow-sm">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="text-sm font-semibold text-stone-900">发信模式</div>
                    <p className="mt-1 text-xs leading-6 text-stone-500">
                      选择本次任务的写信方式。
                    </p>
                  </div>
                  <span className="rounded-full border border-primary/15 bg-primary/8 px-3 py-1 text-[11px] font-semibold text-primary">
                    本次任务
                  </span>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  {MODE_OPTIONS.map((option) => {
                    const active = taskMode === option.value;
                    return (
                      <button
                        key={option.value}
                        type="button"
                        onClick={() => setTaskMode(option.value)}
                        className={[
                          'rounded-[24px] border px-4 py-4 text-left transition',
                          active
                            ? 'border-primary bg-primary text-white shadow-[0_18px_30px_-22px_rgba(154,52,18,0.65)]'
                            : 'border-stone-200 bg-white text-stone-800 hover:border-primary/35 hover:bg-primary/5',
                        ].join(' ')}
                      >
                        <div className="text-sm font-semibold">{option.title}</div>
                        <div className={active ? 'mt-2 text-xs leading-6 text-white/80' : 'mt-2 text-xs leading-6 text-stone-500'}>
                          {option.description}
                        </div>
                      </button>
                    );
                  })}
                </div>
                <div className="mt-3 text-xs leading-6 text-stone-500">
                  本页设置只影响本次任务。
                </div>
                <div className="mt-3 rounded-2xl border border-primary/15 bg-primary/5 px-4 py-3 text-sm leading-6 text-stone-700">
                  {taskMode === 'template'
                    ? scheduleType === 'scheduled'
                      ? '模板内容会直接进入待发送队列，并按批量定时窗口自动发送。'
                      : '模板内容会直接进入发送流程。'
                    : 'AI 改写完成后仍需逐封审核通过，再进入发送流程。'}
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <NativeSelectField
                  label="发送方式"
                  value={scheduleType}
                  onChange={(event) => setScheduleType(event.target.value as 'immediate' | 'scheduled')}
                >
                  <option value="immediate">立即发送</option>
                  <option value="scheduled">定时发送</option>
                </NativeSelectField>

                {scheduleType === 'scheduled' && (
                  <label className="block">
                    <div className="mb-2 text-sm font-medium text-stone-800">窗口内发送数量</div>
                    <input
                      type="number"
                      min="1"
                      value={emailsPerWindow}
                      onChange={(event) => setEmailsPerWindow(event.target.value)}
                      className="form-input"
                    />
                  </label>
                )}
              </div>

              <p className="text-sm leading-6 text-stone-500">
                {scheduleType === 'scheduled'
                  ? '定时发送：在指定时间窗口内按数量发送。'
                  : '立即发送：任务创建后即可进入发送流程。'}
              </p>

              {scheduleType === 'scheduled' && (
                <div className="space-y-5 border-t border-stone-200 pt-5">
                  <TaskDateSelector
                    selectedDates={scheduledDates}
                    onChange={setScheduledDates}
                  />
                  <div className="grid gap-4 md:grid-cols-2">
                    <label className="block">
                      <div className="mb-2 text-sm font-medium text-stone-800">发送开始时间</div>
                      <input
                        type="time"
                        value={startTime}
                        onChange={(event) => setStartTime(event.target.value)}
                        className="form-input"
                      />
                    </label>
                    <label className="block">
                      <div className="mb-2 text-sm font-medium text-stone-800">发送结束时间</div>
                      <input
                        type="time"
                        value={endTime}
                        onChange={(event) => setEndTime(event.target.value)}
                        className="form-input"
                      />
                    </label>
                  </div>
                  <p className="text-xs leading-6 text-stone-500">
                    已选 {normalizeScheduledDates(scheduledDates).length} 天，将在 {startTime || '--:--'} 至{' '}
                    {endTime || '--:--'} 之间动态发送，每天最多 {emailsPerWindow || 0} 封。
                  </p>
                </div>
              )}

              {taskMode === 'llm' ? (
                <div className="space-y-5 rounded-3xl border border-stone-200 bg-stone-50/80 p-4">
                  <div>
                    <div className="text-sm font-semibold text-stone-900">套磁信模板（必填）</div>
                    <p className="mt-1 text-xs leading-6 text-stone-500">
                      AI 基于这份模板生成个性化草稿。
                    </p>
                  </div>
                  <SubjectTemplateInput
                    label="模板主题（可选）"
                    value={subject}
                    onChange={setSubject}
                    placeholder="例如：申请与{{name}}老师交流科研方向"
                  />

                  <EmailTemplateEditor
                    label="模板正文"
                    html={bodyHtml || (body ? textToEmailHtml(body) : '')}
                    onChange={({ html, text }) => {
                      setBodyHtml(html);
                      setBody(text);
                    }}
                  />
                </div>
              ) : (
                <div className="space-y-5 rounded-3xl border border-primary/15 bg-[linear-gradient(180deg,rgba(154,52,18,0.04),rgba(255,255,255,0.95))] p-4">
                  <div>
                    <div className="text-sm font-semibold text-stone-900">直接套用模板</div>
                    <p className="mt-1 text-xs leading-6 text-stone-500">
                      本次任务使用的模板内容。
                    </p>
                  </div>
                  <SubjectTemplateInput
                    label="模板主题"
                    value={templateSubject}
                    onChange={setTemplateSubject}
                    placeholder="例如：申请与{{name}}老师交流科研方向"
                  />
                  <EmailTemplateEditor
                    label="模板正文"
                    html={templateBodyHtml || (templateBodyText ? textToEmailHtml(templateBodyText) : '')}
                    onChange={({ html, text }) => {
                      setTemplateBodyHtml(html);
                      setTemplateBodyText(text);
                    }}
                  />
                </div>
              )}

              {taskMode === 'llm' ? (
                <div className="rounded-3xl border border-stone-200 bg-stone-50 p-4">
                  <div className="text-sm font-medium text-stone-900">AI 写信参考材料</div>
                  <p className="mt-1 text-xs text-stone-500">AI 会基于这份主材料生成或改写草稿。</p>
                  {primaryMaterialOptions.length === 0 ? (
                    <p className="mt-3 text-sm text-stone-500">
                      暂无可用参考材料，请先在身份中设置可用于 AI 写信的主材料。
                    </p>
                  ) : (
                    <div className="mt-3 space-y-2">
                      {primaryMaterialOptions.map((material) => (
                        <label
                          key={material.id}
                          className="flex items-center justify-between gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-700"
                        >
                          <span className="flex items-center gap-3">
                            <input
                              type="radio"
                              name="primary-material"
                              checked={primaryMaterialId === material.id}
                              onChange={() => setPrimaryMaterialId(material.id)}
                            />
                            <span>{material.display_name}</span>
                          </span>
                          <span className="text-xs text-stone-500">
                            {MATERIAL_TYPE_LABELS[material.material_type]}
                          </span>
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              ) : null}

              <div className="rounded-3xl border border-stone-200 bg-stone-50 p-4">
                <div className="text-sm font-medium text-stone-900">随信附件</div>
                <p className="mt-1 text-xs text-stone-500">随邮件一起发送。</p>
                {selectedIdentity.materials.length === 0 ? (
                  <p className="mt-3 text-sm text-stone-500">暂无可选材料。</p>
                ) : (
                  <div className="mt-3 space-y-2">
                    {selectedIdentity.materials.map((material) => {
                      const checked = selectedMaterialIds.includes(material.id);
                      return (
                        <label
                          key={material.id}
                          className="flex items-center justify-between gap-3 rounded-2xl border border-stone-200 bg-white px-4 py-3 text-sm text-stone-700"
                        >
                          <span className="flex items-center gap-3">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => {
                                setSelectedMaterialIds((previous) =>
                                  previous.includes(material.id)
                                    ? previous.filter((item) => item !== material.id)
                                    : [...previous, material.id],
                                );
                              }}
                            />
                            <span>{material.display_name}</span>
                          </span>
                          <span className="text-xs text-stone-500">
                            {MATERIAL_TYPE_LABELS[material.material_type]}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                )}
              </div>

              <div className="flex flex-wrap gap-3">
                <button type="button" onClick={() => navigate('/')} className="ui-btn-secondary">
                  返回首页
                </button>
                <button
                  type="button"
                  onClick={() => void handleSubmit()}
                  disabled={submitting}
                  className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
                  创建任务
                </button>
              </div>
            </div>
          </section>

          <aside className="rounded-3xl border border-stone-200 bg-white p-6 shadow-sm">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-lg font-semibold text-stone-900">目标导师</h2>
                <div className="mt-1 text-xs text-stone-500">共 {professors.length} 位</div>
              </div>
              {targetMentorsTotalPages > 1 ? (
                <div className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1 text-xs font-medium text-stone-600">
                  {targetMentorsPage} / {targetMentorsTotalPages} 页
                </div>
              ) : null}
            </div>
            <div className="mt-4 space-y-3">
              {visibleTargetMentors.map((professor) => (
                <div key={professor.id} className="rounded-2xl border border-stone-100 bg-stone-50 px-4 py-3">
                  <div className="font-medium text-stone-900">{professor.name}</div>
                  <div className="mt-1 text-sm text-stone-500">
                    {[professor.title, professor.university].filter(Boolean).join(' / ')}
                  </div>
                  <div className="mt-2 text-xs text-stone-500">
                    匹配分数：{professor.match_score === null ? '未计算' : `${professor.match_score}%`}
                  </div>
                </div>
              ))}
            </div>
            {targetMentorsTotalPages > 1 ? (
              <div className="mt-4 flex items-center justify-between gap-3 border-t border-stone-100 pt-4">
                <button
                  type="button"
                  disabled={targetMentorsPage <= 1}
                  onClick={() => setTargetMentorsPage((currentPage) => Math.max(1, currentPage - 1))}
                  className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-50"
                >
                  上一页
                </button>
                <button
                  type="button"
                  disabled={targetMentorsPage >= targetMentorsTotalPages}
                  onClick={() =>
                    setTargetMentorsPage((currentPage) => Math.min(targetMentorsTotalPages, currentPage + 1))
                  }
                  className="ui-btn-secondary disabled:cursor-not-allowed disabled:opacity-50"
                >
                  下一页
                </button>
              </div>
            ) : null}
          </aside>
          </div>
        )}
      </main>
      {confirmDialog}
    </>
  );
};
