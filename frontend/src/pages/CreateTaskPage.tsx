import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useRef } from 'react';
import { Loader2 } from 'lucide-react';
import { NativeSelectField } from '@/components/atoms/NativeSelectField';
import { AttachmentSizeSummary } from '@/components/molecules/AttachmentSizeSummary';
import { EmailTemplateEditor } from '@/components/molecules/EmailTemplateEditor';
import { Pagination } from '@/components/molecules/Pagination';
import { SelectionToggleButton } from '@/components/molecules/SelectionToggleButton';
import { SubjectTemplateInput } from '@/components/molecules/SubjectTemplateInput';
import { TaskDateSelector } from '@/components/molecules/TaskDateSelector';
import { useNotification } from '@/context/NotificationContext';
import { safeRecordUserAction } from '@/lib/diagnosticUserActions';
import { createBatchTask } from '@/lib/api/batchTasksApi';
import { listOutreachTemplates } from '@/lib/api/outreachTemplates';
import {
  clearCreateTaskNavigationHandoff,
  clearCreateTaskResendContext,
  readCreateTaskNavigationHandoff,
  writeCreateTaskNavigationHandoff,
} from '@/features/navigation-handoffs/client/navigationHandoff';
import { listProfessors } from '@/entities/professor/api/professors';
import { getPageItems, getTotalPages, PAGE_SIZE } from '@/lib/pagination';
import { textToEmailHtml } from '@/lib/richEmail';
import { usePaginationState } from '@/lib/usePaginationState';
import { useSelectionContext } from '@/context/SelectionContext';
import { getTaskModeCopy } from '@/features/create-task/client/taskCopy';
import { buildBatchCreateConfirmDescription } from '@/features/create-task/client/batchCreateConfirmDescription';
import {
  buildLargeAttachmentWarning,
  formatFileSize,
  getSelectedAttachmentTotalBytes,
  LARGE_ATTACHMENT_WARNING_CONFIRMATION_LABEL,
  shouldPromptForLargeAttachments,
  suppressLargeAttachmentWarnings,
} from '@/features/attachments/attachmentSize';
import {
  hasFutureScheduleWindow,
  normalizeScheduledDates,
} from '@/features/create-task/client/scheduleDates';
import { useConfirmDialog } from '@/lib/useConfirmDialog';
import {
  MATERIAL_TYPE_LABELS,
  type BatchTaskResendContentStrategy,
  type IdentityMaterialDTO,
  type OutreachGenerationMode,
  type OutreachTemplateDTO,
  type ProfessorDashboardItemDTO,
} from '@/types';

const PRIMARY_MATERIAL_EXTENSIONS = ['.pdf', '.doc', '.docx', '.txt', '.md'];
const TARGET_MENTORS_PAGE_SIZE_STORAGE_KEY = 'create-task:target-mentors:page-size';

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

const RESEND_STRATEGY_OPTIONS: Array<{
  value: BatchTaskResendContentStrategy;
  title: string;
  description: string;
}> = [
  {
    value: 'reuse',
    title: '沿用上次内容',
    description: '保留已有邮件；缺失或未审核的仍需处理。',
  },
  {
    value: 'template',
    title: '重新套用模板',
    description: '用当前模板替换旧正文，直接进入发送计划。',
  },
  {
    value: 'llm',
    title: 'AI 重新改写',
    description: '重新生成全部草稿，并逐封审核。',
  },
];

export const CreateTaskPage = () => {
  const navigate = useNavigate();
  const { notifyError, notifyFormErrors } = useNotification();
  const { confirm, dialog: confirmDialog } = useConfirmDialog();
  const { selectedIdentityId, selectedLlmProfileId, selectedIdentity } = useSelectionContext();
  const [navigationHandoff] = useState(() => readCreateTaskNavigationHandoff());
  const selectedProfessorIds = useMemo(
    () => navigationHandoff?.professorIds ?? [],
    [navigationHandoff],
  );
  const resendPrefillContext = navigationHandoff?.resendContext ?? null;
  const [professors, setProfessors] = useState<ProfessorDashboardItemDTO[]>([]);
  const {
    page: targetMentorsPage,
    pageSize: targetMentorsPageSize,
    setPage: setTargetMentorsPage,
    onChange: handleTargetMentorsPaginationChange,
  } = usePaginationState({
    storageKey: TARGET_MENTORS_PAGE_SIZE_STORAGE_KEY,
    initialPageSize: PAGE_SIZE,
  });
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [taskName, setTaskName] = useState(`批量任务 ${new Date().toLocaleDateString('zh-CN')}`);
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [bodyHtml, setBodyHtml] = useState('');
  const [taskMode, setTaskMode] = useState<OutreachGenerationMode>('llm');
  const [resendContentStrategy, setResendContentStrategy] =
    useState<BatchTaskResendContentStrategy>('reuse');
  const [templateSubject, setTemplateSubject] = useState('');
  const [templateBodyText, setTemplateBodyText] = useState('');
  const [templateBodyHtml, setTemplateBodyHtml] = useState('');
  const [outreachTemplates, setOutreachTemplates] = useState<OutreachTemplateDTO[]>([]);
  const [loadingOutreachTemplates, setLoadingOutreachTemplates] = useState(true);
  const [outreachTemplatesLoaded, setOutreachTemplatesLoaded] = useState(false);
  const [selectedOutreachTemplateId, setSelectedOutreachTemplateId] = useState<number | null>(null);
  const activeOutreachTemplates = useMemo(
    () => outreachTemplates.filter((template) => !template.archived_at),
    [outreachTemplates],
  );
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
  const templateInitializationKeyRef = useRef<string | null>(null);
  const resendCleanupTimeoutRef = useRef<number | null>(null);
  const targetMentorsStartRef = useRef<HTMLElement | null>(null);
  const isResendPrefillActive =
    resendPrefillContext !== null && resendPrefillContext.identityId === selectedIdentityId;
  const requiresDraftGeneration =
    !isResendPrefillActive ||
    resendContentStrategy !== 'reuse' ||
    resendPrefillContext?.requiresRegeneration !== false;
  const professorsRequestKey =
    selectedIdentityId && selectedProfessorIds.length > 0
      ? `${selectedIdentityId}:${selectedProfessorIds.join(',')}`
      : null;

  useEffect(() => {
    if (resendCleanupTimeoutRef.current !== null) {
      window.clearTimeout(resendCleanupTimeoutRef.current);
      resendCleanupTimeoutRef.current = null;
    }
    return () => {
      resendCleanupTimeoutRef.current = window.setTimeout(() => {
        resendCleanupTimeoutRef.current = null;
        clearCreateTaskResendContext();
      }, 0);
    };
  }, []);

  useEffect(() => {
    let ignore = false;
    const loadTemplates = async () => {
      setLoadingOutreachTemplates(true);
      try {
        const templates = await listOutreachTemplates(true);
        if (!ignore) {
          setOutreachTemplates(templates);
          setOutreachTemplatesLoaded(true);
        }
      } catch (loadError) {
        if (!ignore) {
          setOutreachTemplatesLoaded(false);
          notifyError(
            '加载发信模板失败',
            loadError instanceof Error ? loadError.message : '加载发信模板失败',
          );
        }
      } finally {
        if (!ignore) {
          setLoadingOutreachTemplates(false);
        }
      }
    };
    void loadTemplates();
    return () => {
      ignore = true;
    };
  }, [notifyError]);

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
      templateInitializationKeyRef.current = null;
      setSelectedOutreachTemplateId(null);
      setPrimaryMaterialId(null);
      setSelectedMaterialIds([]);
      setTaskMode('llm');
      setResendContentStrategy('reuse');
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
    setSelectedOutreachTemplateId(selectedIdentity.default_outreach_template_id ?? null);

    if (isResendPrefillActive && resendPrefillContext) {
      setTaskName('重新发起 - ' + resendPrefillContext.sourceTaskName);
      const mode = resendPrefillContext.defaults.outreach_generation_mode ?? selectedIdentity.outreach_generation_mode ?? 'llm';
      const defaultResendStrategy: BatchTaskResendContentStrategy =
        mode === 'template' ? 'template' : 'reuse';
      const subjectValue = resendPrefillContext.defaults.outreach_template_subject ?? '';
      const bodyTextValue = resendPrefillContext.defaults.outreach_template_body_text ?? '';
      const bodyHtmlValue = resendPrefillContext.defaults.outreach_template_body_html ?? (bodyTextValue ? textToEmailHtml(bodyTextValue) : '');
      const materialIds = new Set(selectedIdentity.materials.map((material) => material.id));
      setTaskMode(mode);
      setResendContentStrategy(defaultResendStrategy);
      setSubject(subjectValue);
      setBody(bodyTextValue);
      setBodyHtml(bodyHtmlValue);
      setTemplateSubject(subjectValue);
      setTemplateBodyText(bodyTextValue);
      setTemplateBodyHtml(bodyHtmlValue);
      setSelectedOutreachTemplateId(
        resendPrefillContext.defaults.outreach_template_id ?? null,
      );
      setPrimaryMaterialId(
        resendPrefillContext.defaults.primary_material_id !== null &&
          materialIds.has(resendPrefillContext.defaults.primary_material_id)
          ? resendPrefillContext.defaults.primary_material_id
          : null,
      );
      setSelectedMaterialIds(resendPrefillContext.defaults.selected_material_ids.filter((id) => materialIds.has(id)));
    } else if (resendPrefillContext) {
      writeCreateTaskNavigationHandoff(selectedProfessorIds);
    }
  }, [
    isResendPrefillActive,
    resendPrefillContext,
    selectedIdentity,
    selectedProfessorIds,
  ]);

  useEffect(() => {
    if (!selectedIdentity || loadingOutreachTemplates || !outreachTemplatesLoaded) {
      return;
    }
    const initializationKey = `${selectedIdentity.id}:${
      isResendPrefillActive && resendPrefillContext
        ? `resend-${resendPrefillContext.sourceTaskId}`
        : 'new-task'
    }`;
    if (templateInitializationKeyRef.current === initializationKey) {
      return;
    }
    templateInitializationKeyRef.current = initializationKey;

    if (isResendPrefillActive && resendPrefillContext) {
      const snapshotTemplateId =
        resendPrefillContext.defaults.outreach_template_id ?? null;
      const snapshotTemplate =
        snapshotTemplateId === null
          ? null
          : outreachTemplates.find(
              (template) => template.id === snapshotTemplateId,
            ) ?? null;
      setSelectedOutreachTemplateId(
        snapshotTemplate
          ? snapshotTemplateId
          : null,
      );
      if (
        resendPrefillContext.defaults.outreach_generation_mode === 'template' &&
        snapshotTemplate &&
        !snapshotTemplate.archived_at
      ) {
        const nextBodyText = snapshotTemplate.body_text ?? '';
        const nextBodyHtml =
          snapshotTemplate.body_html ??
          (nextBodyText ? textToEmailHtml(nextBodyText) : '');
        setSubject(snapshotTemplate.subject ?? '');
        setBody(nextBodyText);
        setBodyHtml(nextBodyHtml);
        setTemplateSubject(snapshotTemplate.subject ?? '');
        setTemplateBodyText(nextBodyText);
        setTemplateBodyHtml(nextBodyHtml);
      }
      return;
    }

    const selectedTemplate =
      activeOutreachTemplates.find(
        (template) =>
          template.id === selectedIdentity.default_outreach_template_id,
      ) ??
      activeOutreachTemplates.find((template) => template.is_default) ??
      null;
    if (!selectedTemplate) {
      return;
    }

    const nextBodyText = selectedTemplate.body_text ?? '';
    const nextBodyHtml =
      selectedTemplate.body_html ??
      (nextBodyText ? textToEmailHtml(nextBodyText) : '');
    setSelectedOutreachTemplateId(selectedTemplate.id);
    setTaskMode(selectedTemplate.recommended_generation_mode);
    setSubject(selectedTemplate.subject ?? '');
    setBody(nextBodyText);
    setBodyHtml(nextBodyHtml);
    setTemplateSubject(selectedTemplate.subject ?? '');
    setTemplateBodyText(nextBodyText);
    setTemplateBodyHtml(nextBodyHtml);
  }, [
    isResendPrefillActive,
    activeOutreachTemplates,
    loadingOutreachTemplates,
    outreachTemplatesLoaded,
    outreachTemplates,
    resendPrefillContext,
    selectedIdentity,
  ]);

  const applySelectedOutreachTemplate = (templateId: number | null) => {
    setSelectedOutreachTemplateId(templateId);
    if (templateId === null) {
      return;
    }
    const template = outreachTemplates.find((item) => item.id === templateId);
    if (!template) {
      return;
    }
    const nextBodyText = template.body_text ?? '';
    const nextBodyHtml =
      template.body_html ?? (nextBodyText ? textToEmailHtml(nextBodyText) : '');
    setTaskMode(
      isResendPrefillActive && resendContentStrategy !== 'reuse'
        ? resendContentStrategy
        : template.recommended_generation_mode,
    );
    setSubject(template.subject ?? '');
    setBody(nextBodyText);
    setBodyHtml(nextBodyHtml);
    setTemplateSubject(template.subject ?? '');
    setTemplateBodyText(nextBodyText);
    setTemplateBodyHtml(nextBodyHtml);
  };

  const handleSelectResendContentStrategy = (
    strategy: BatchTaskResendContentStrategy,
  ) => {
    if (
      strategy !== 'reuse' &&
      resendContentStrategy === 'reuse' &&
      !requiresDraftGeneration
    ) {
      const selectedTemplate = outreachTemplates.find(
        (template) =>
          template.id === selectedOutreachTemplateId && !template.archived_at,
      );
      if (selectedTemplate) {
        const nextBodyText = selectedTemplate.body_text ?? '';
        const nextBodyHtml =
          selectedTemplate.body_html ??
          (nextBodyText ? textToEmailHtml(nextBodyText) : '');
        setSubject(selectedTemplate.subject ?? '');
        setBody(nextBodyText);
        setBodyHtml(nextBodyHtml);
        setTemplateSubject(selectedTemplate.subject ?? '');
        setTemplateBodyText(nextBodyText);
        setTemplateBodyHtml(nextBodyHtml);
      }
    }
    setResendContentStrategy(strategy);
    if (strategy === 'reuse') {
      setTaskMode(
        resendPrefillContext?.defaults.outreach_generation_mode ??
          selectedIdentity?.outreach_generation_mode ??
          'llm',
      );
      return;
    }
    setTaskMode(strategy);
  };

  const primaryMaterialOptions = useMemo(
    () => (selectedIdentity ? selectedIdentity.materials.filter(isPrimaryMaterialCandidate) : []),
    [selectedIdentity],
  );
  const selectedAttachmentTotalBytes = useMemo(
    () =>
      getSelectedAttachmentTotalBytes(
        selectedIdentity?.materials ?? [],
        selectedMaterialIds,
      ),
    [selectedIdentity, selectedMaterialIds],
  );
  const targetMentorsTotalPages = getTotalPages(
    professors.length,
    targetMentorsPageSize,
  );
  const safeTargetMentorsPage = Math.min(
    targetMentorsPage,
    targetMentorsTotalPages,
  );
  const visibleTargetMentors = useMemo(
    () =>
      getPageItems(
        professors,
        safeTargetMentorsPage,
        targetMentorsPageSize,
      ),
    [professors, safeTargetMentorsPage, targetMentorsPageSize],
  );

  useEffect(() => {
    setTargetMentorsPage(1);
  }, [professorsRequestKey, setTargetMentorsPage]);

  useEffect(() => {
    setTargetMentorsPage((currentPage) => Math.min(currentPage, targetMentorsTotalPages));
  }, [setTargetMentorsPage, targetMentorsTotalPages]);

  const selectedOutreachTemplate =
    outreachTemplates.find(
      (template) => template.id === selectedOutreachTemplateId,
    ) ?? null;
  const resendTemplateName = selectedOutreachTemplate?.name ?? '当前模板内容';
  const resendOutcomeDescription = isResendPrefillActive
    ? resendContentStrategy === 'template'
      ? `${professors.length} 封将套用「${resendTemplateName}」，${
          scheduleType === 'scheduled' ? '按计划自动发送' : '进入发送流程'
        }。`
      : resendContentStrategy === 'llm'
        ? `${professors.length} 封将由 AI 重新改写并逐封审核。`
        : resendPrefillContext?.requiresRegeneration
          ? `${professors.length} 封优先沿用上次内容；缺失内容将重新生成。`
          : `${professors.length} 封沿用上次内容；未审核内容仍需处理。`
    : null;
  const templateSelectionPanel = (
    <div className="rounded-[28px] border border-stone-200 bg-stone-50/80 p-4">
      <NativeSelectField
        label="选择模板"
        value={selectedOutreachTemplateId === null ? '' : String(selectedOutreachTemplateId)}
        disabled={loadingOutreachTemplates}
        onChange={(event) =>
          applySelectedOutreachTemplate(
            event.target.value ? Number(event.target.value) : null,
          )
        }
      >
        <option value="">不选模板，直接编辑</option>
        {selectedOutreachTemplate?.archived_at ? (
          <option value={selectedOutreachTemplate.id} disabled>
            {selectedOutreachTemplate.name} · 已删除（仅保留历史来源）
          </option>
        ) : null}
        {activeOutreachTemplates.map((template) => (
          <option key={template.id} value={template.id}>
            {template.name}{template.is_default ? ' · 全局默认' : ''}{template.is_ready ? '' : ' · 内容待完善'}
          </option>
        ))}
      </NativeSelectField>
      <p className="mt-2 text-xs leading-6 text-stone-500">
        {loadingOutreachTemplates
          ? '正在加载模板库…'
          : selectedOutreachTemplate
            ? `已带入“${selectedOutreachTemplate.name}”；本次修改不影响模板库。`
            : '本次内容将独立保存。'}
      </p>
    </div>
  );

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
      validationErrors.push('没有可创建任务的导师');
    }
    if (scheduleType === 'scheduled' && normalizedScheduledDates.length === 0) {
      validationErrors.push('请选择发送日期');
    }
    if (scheduleType === 'scheduled' && (!startTime || !endTime || !emailsPerWindow)) {
      validationErrors.push('请填写发送时段和每日发送上限');
    }
    if (
      scheduleType === 'scheduled' &&
      endTime &&
      normalizedScheduledDates.length > 0 &&
      !hasFutureScheduleWindow(normalizedScheduledDates, endTime)
    ) {
      validationErrors.push('所选发送时段已过期，请重新选择日期或结束时间');
    }
    if (requiresDraftGeneration && taskMode === 'template' && !templateSubject.trim()) {
      validationErrors.push('请填写模板主题');
    }
    if (requiresDraftGeneration && taskMode === 'template' && !templateBodyText.trim()) {
      validationErrors.push('请填写模板正文');
    }
    if (requiresDraftGeneration && taskMode === 'llm' && !subject.trim()) {
      validationErrors.push('请填写邮件模板主题');
    }
    if (requiresDraftGeneration && taskMode === 'llm' && !body.trim()) {
      validationErrors.push('请填写邮件模板正文');
    }
    if (requiresDraftGeneration && taskMode === 'llm' && primaryMaterialId === null) {
      validationErrors.push('请选择 AI 参考材料');
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

    const confirmTemplateName =
      selectedOutreachTemplate?.name ??
      (selectedOutreachTemplateId !== null ? '当前已选模板' : null);
    const baseConfirmDescription =
      resendOutcomeDescription ??
      buildBatchCreateConfirmDescription(
        taskMode,
        scheduleType,
        confirmTemplateName,
      );
    const attachmentWarning = shouldPromptForLargeAttachments()
      ? buildLargeAttachmentWarning(
          selectedAttachmentTotalBytes,
          { repeatedPerMessage: true },
        )
      : null;
    const attachmentOverRecommendedLimit = Boolean(attachmentWarning);
    const confirmDescription = [baseConfirmDescription, attachmentWarning]
      .filter(Boolean)
      .join('\n');

    const confirmed = await confirm({
      title: attachmentOverRecommendedLimit
        ? '附件超过 1 MB，仍要创建批量任务吗？'
        : scheduleType === 'scheduled'
          ? '确认创建定时批量发送任务？'
          : '确认创建真实发送任务？',
      description: confirmDescription,
      confirmLabel: attachmentOverRecommendedLimit ? '仍然创建' : '继续创建',
      cancelLabel: attachmentOverRecommendedLimit ? '返回调整' : '再检查一下',
      confirmationCheckbox: attachmentWarning
        ? {
            label: LARGE_ATTACHMENT_WARNING_CONFIRMATION_LABEL,
            onConfirmChecked: suppressLargeAttachmentWarnings,
          }
        : undefined,
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
        outreach_template_id: selectedOutreachTemplateId,
        ...(isResendPrefillActive && resendPrefillContext
          ? {
              resend_source_batch_task_id: resendPrefillContext.sourceTaskId,
              resend_content_strategy: resendContentStrategy,
            }
          : {}),
      });
      safeRecordUserAction({
        eventName: 'tasks.batch_create_succeeded',
        data: diagnosticData,
      });
      clearCreateTaskNavigationHandoff();
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
              已从「{resendPrefillContext.sourceTaskName}」带入 {resendPrefillContext.professorIds.length} 位导师；请重新选择内容策略和发送时间。
              {resendPrefillContext.warnings.map((warning) => (
                <span key={warning} className="mt-1 block text-xs text-amber-800">{warning}</span>
              ))}
            </div>
          ) : null}
        </div>

        {loading ? (
          <div className="mt-6 flex items-center justify-center gap-2 rounded-3xl border border-stone-200 bg-white px-6 py-14 text-sm text-stone-500 shadow-sm">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在加载已选导师…
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

              {!isResendPrefillActive ? templateSelectionPanel : null}

              <div className="rounded-[28px] border border-stone-200 bg-[linear-gradient(180deg,rgba(255,248,240,0.72),rgba(255,255,255,0.96))] p-4 shadow-sm">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="text-sm font-semibold text-stone-900">
                      {isResendPrefillActive ? '重发内容' : '写信方式'}
                    </div>
                    {isResendPrefillActive ? (
                      <p className="mt-1 text-xs leading-6 text-stone-500">
                        选择这次重新发起时要使用的内容。
                      </p>
                    ) : null}
                  </div>
                </div>
                {isResendPrefillActive ? (
                  <div
                    role="radiogroup"
                    aria-label="重发内容策略"
                    className="mt-4 grid gap-3 md:grid-cols-3"
                  >
                    {RESEND_STRATEGY_OPTIONS.map((option) => {
                      const active = resendContentStrategy === option.value;
                      return (
                        <button
                          key={option.value}
                          type="button"
                          role="radio"
                          aria-checked={active}
                          onClick={() => handleSelectResendContentStrategy(option.value)}
                          className={[
                            'rounded-[24px] border px-4 py-4 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
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
                ) : (
                  <div className="mt-4 grid gap-3 md:grid-cols-2">
                    {MODE_OPTIONS.map((option) => {
                      const active = taskMode === option.value;
                      return (
                        <button
                          key={option.value}
                          type="button"
                          aria-pressed={active}
                          onClick={() => setTaskMode(option.value)}
                          className={[
                            'rounded-[24px] border px-4 py-4 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
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
                )}
                <div className="mt-3 rounded-2xl border border-primary/15 bg-primary/5 px-4 py-3 text-sm leading-6 text-stone-700">
                  {resendOutcomeDescription ?? (taskMode === 'template'
                    ? scheduleType === 'scheduled'
                      ? '套用模板后按计划自动发送。'
                      : '套用模板后进入发送流程。'
                    : '生成 AI 草稿，审核后进入发送流程。')}
                </div>
              </div>

              {isResendPrefillActive && requiresDraftGeneration
                ? templateSelectionPanel
                : null}

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
                    <div className="mb-2 text-sm font-medium text-stone-800">每日发送上限</div>
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
                  ? '按所选时间窗口和数量发送。'
                  : '创建后立即进入发送流程。'}
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
                    已选 {normalizeScheduledDates(scheduledDates).length} 天 · 每天 {startTime || '--:--'}–
                    {endTime || '--:--'} 发送 · 最多 {emailsPerWindow || 0} 封
                  </p>
                </div>
              )}

              {requiresDraftGeneration ? (
                taskMode === 'llm' ? (
                  <div className="space-y-5 rounded-3xl border border-stone-200 bg-stone-50/80 p-4">
                    <div>
                      <div className="text-sm font-semibold text-stone-900">邮件模板（必填）</div>
                      <p className="mt-1 text-xs leading-6 text-stone-500">
                        AI 基于这份模板生成个性化草稿。
                      </p>
                    </div>
                    <SubjectTemplateInput
                      label="模板主题"
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
                        可直接编辑模板内容。
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
                )
              ) : null}

              {requiresDraftGeneration && taskMode === 'llm' ? (
                <div className="rounded-3xl border border-stone-200 bg-stone-50 p-4">
                  <div className="text-sm font-medium text-stone-900">AI 参考材料</div>
                  <p className="mt-1 text-xs text-stone-500">AI 会基于这份主材料生成或改写草稿。</p>
                  {primaryMaterialOptions.length === 0 ? (
                    <p className="mt-3 text-sm text-stone-500">
                      暂无参考材料，请先为身份设置主材料。
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
                            {MATERIAL_TYPE_LABELS[material.material_type]} · {formatFileSize(material.size_bytes)}
                          </span>
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              ) : null}

              <div className="rounded-3xl border border-stone-200 bg-stone-50 p-4">
                <div className="text-sm font-medium text-stone-900">随信附件</div>
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
                            <SelectionToggleButton
                              label={`选择附件 ${material.display_name}`}
                              selected={checked}
                              semantics="checkbox"
                              size="md"
                              onToggle={() => {
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
                            {MATERIAL_TYPE_LABELS[material.material_type]} · {formatFileSize(material.size_bytes)}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                )}
                <AttachmentSizeSummary
                  selectedCount={selectedMaterialIds.length}
                  totalSizeBytes={selectedAttachmentTotalBytes}
                  className="mt-3"
                />
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

          <aside
            ref={targetMentorsStartRef}
            tabIndex={-1}
            aria-label="目标导师列表"
            className="scroll-mt-24 rounded-3xl border border-stone-200 bg-white p-6 shadow-sm focus:outline-none"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-lg font-semibold text-stone-900">目标导师</h2>
                <div className="mt-1 text-xs text-stone-500">共 {professors.length} 位</div>
              </div>
            </div>
            <div className="mt-4 space-y-3">
              {visibleTargetMentors.map((professor) => (
                <div key={professor.id} className="rounded-2xl border border-stone-100 bg-stone-50 px-4 py-3">
                  <div className="font-medium text-stone-900">{professor.name}</div>
                  <div className="mt-1 text-sm text-stone-500">
                    {[professor.title, professor.university].filter(Boolean).join(' / ')}
                  </div>
                  <div className="mt-2 text-xs text-stone-500">
                    匹配分数：{professor.match_score === null ? '未计算' : professor.match_score}
                  </div>
                </div>
              ))}
            </div>
            {professors.length > 0 ? (
              <Pagination
                page={safeTargetMentorsPage}
                pageSize={targetMentorsPageSize}
                totalCount={professors.length}
                onChange={handleTargetMentorsPaginationChange}
                ariaLabel="目标导师分页"
                variant="compact"
                unitLabel="位"
                itemLabel="位导师"
                focusTargetRef={targetMentorsStartRef}
                menuPlacement="inline"
                className="mt-4 border-t border-stone-100 pt-4"
              />
            ) : null}
          </aside>
          </div>
        )}
      </main>
      {confirmDialog}
    </>
  );
};
