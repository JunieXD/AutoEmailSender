import { AgentSupportCard } from "@/components/molecules/AgentSupportCard";
import { OtherSettingsCard } from "@/components/molecules/OtherSettingsCard";
import { ProjectAcknowledgements } from "@/components/molecules/ProjectAcknowledgements";
import { CommunicationSharingPanel } from "@/components/organisms/CommunicationSharingPanel";
import { DiagnosticLogPanel } from "@/components/organisms/DiagnosticLogPanel";
import { useDesktopBackend } from "@/context/DesktopBackendContext";
import { useNotification } from "@/context/NotificationContext";
import { useSelectionContext } from "@/context/SelectionContext";
import { useWorkspaceDraftGuard } from "@/context/useWorkspaceDraftGuard";
import {
  ActionResultState,
  IdentityConnectionTestSummary,
  TestComposeSetupStatus,
  getActionButtonClassName,
  inputClassName,
} from "@/features/profile-setup/model/formControls";
import { isIdentityDeletionImpact } from "@/features/profile-setup/model/identityDeletion";
import { isDeletionImpact } from "@/features/profile-setup/model/llmDeletion";
import { ApiError } from "@/lib/api/client";
import {
  createIdentity,
  deleteIdentity,
  getIdentityDeletionImpact,
  importIdentityTemplate,
  setDefaultIdentity,
  testIdentityImap,
  testIdentitySmtp,
  updateIdentity,
  updateIdentityDefaultOutreachTemplate,
} from "@/lib/api/identities";
import {
  createLLMProfile,
  deleteLLMProfile,
  fetchLLMProfileModelsPreview,
  getLLMProfileDeletionImpact,
  setDefaultLLMProfile,
  testLLMProfilePreview,
  updateLLMProfile,
} from "@/lib/api/llmProfiles";
import {
  deleteMaterial,
  downloadMaterial,
  getMaterialDeletionImpact,
  setPrimaryMaterial,
  uploadIdentityMaterial,
} from "@/lib/api/materials";
import {
  archiveOutreachTemplate,
  createOutreachTemplate,
  duplicateOutreachTemplate,
  listOutreachTemplates,
  restoreOutreachTemplate,
  setGlobalDefaultOutreachTemplate,
  updateOutreachTemplate,
} from "@/lib/api/outreachTemplates";
import { getTestComposeStatus } from "@/lib/api/testComposeApi";
import { isDesktopApp, openDesktopMaterial } from "@/lib/desktopApi";
import { PROFILE_HELP_LINKS } from "@/lib/helpLinks";
import { useConfirmDialog } from "@/lib/useConfirmDialog";
import { useDocumentScrollLock } from "@/lib/useDocumentScrollLock";
import {
  type IdentityDTO,
  type IdentityDeletionImpactDTO,
  type IdentityMaterialDTO,
  type IdentityMaterialType,
  type LLMProfileDeletionImpactDTO,
  type LLMProfileModelsResultDTO,
  type LLMProfileTestResultDTO,
  type OutreachTemplateDTO,
} from "@/types";
import clsx from "clsx";
import {
  CheckCircle2,
  Eye,
  EyeOff,
  Loader2,
  Send,
  Trash2,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { buildMaterialDeletionConfirmationDescription } from "./client/materialDeletionImpact";
import { EditorSwitcher } from "./components/editorSwitcher";
import {
  ContextualHelpLink,
  FieldLabel,
  FormFieldHeader,
  ProfileSetupSection,
} from "./components/formControls";
import { IdentityConnectionCard } from "./components/identityConnection";
import { IdentityDeletionDialog } from "./components/identityDeletion";
import { LLMDeletionDialog } from "./components/llmDeletion";
import {
  LlmModelsFeedbackPanel,
  LlmTestFeedbackPanel,
} from "./components/llmFeedback";
import {
  MaterialLibraryModal,
  MaterialSummaryCard,
} from "./components/materials";
import {
  OutreachTemplateModal,
  OutreachTemplateSummaryCard,
} from "./components/templates";
import { getMaterialTypeLabel } from "./model/materialLabels";
import {
  DEFAULT_LLM_MAX_TOKENS,
  DEFAULT_LLM_TEMPERATURE,
  PROFILE_SETUP_STAGES,
  applyOutreachTemplateToIdentityForm,
  areIdentityFormsEqual,
  clearOutreachTemplateFromIdentityForm,
  createEmptyIdentityForm,
  createEmptyLLMForm,
  createEmptyOutreachTemplateForm,
  getIdentityProfileName,
  hasVisibleTemplateBody,
  inferImapHost,
  isExistingEditorId,
  shouldSyncImapHost,
  toIdentityForm,
  toIdentityPayload,
  toLLMForm,
  toLLMPayload,
  toOutreachTemplateForm,
  toOutreachTemplatePayload,
  type EditorId,
  type IdentityFormState,
  type LLMFormState,
  type MaterialFilterValue,
  type OutreachTemplateFormState,
  type ProfileSetupItem,
  type ProfileSetupSectionId,
} from "./model/profileForms";

export const ProfilePage = () => {
  const {
    identities,
    llmProfiles,
    selectedIdentityId,
    selectedLlmProfileId,
    selectedIdentity,
    selectedLlmProfile,
    setSelectedIdentityId,
    setSelectedLlmProfileId,
    refreshSelections,
    loading,
  } = useSelectionContext();
  const { registerWorkspaceDraftGuard, requestWorkspaceDraftGuard } =
    useWorkspaceDraftGuard();
  const { notifyError, notifyFormErrors, notifySuccess } = useNotification();
  const { isReady: desktopBackendReady, disableReason: desktopDisableReason } =
    useDesktopBackend();
  const [identityEditorId, setIdentityEditorId] = useState<EditorId>(null);
  const [llmEditorId, setLlmEditorId] = useState<EditorId>(null);
  const [identityForm, setIdentityForm] = useState<IdentityFormState>(
    createEmptyIdentityForm(),
  );
  const identityFormRef = useRef(identityForm);
  const identityFormBaselineRef = useRef(identityForm);
  const identityEditorIdRef = useRef<EditorId>(identityEditorId);
  const identityEditorSelectionIdRef = useRef<number | null>(
    selectedIdentityId,
  );
  const selectedIdentityIdRef = useRef<number | null>(selectedIdentityId);
  const saveIdentityRef = useRef<
    (options?: { silent?: boolean }) => Promise<IdentityDTO | null>
  >(async () => null);
  const [smtpPasswordVisible, setSmtpPasswordVisible] = useState(false);
  const [outreachTemplates, setOutreachTemplates] = useState<
    OutreachTemplateDTO[]
  >([]);
  const [loadingOutreachTemplates, setLoadingOutreachTemplates] =
    useState(true);
  const [templateEditorId, setTemplateEditorId] = useState<EditorId>(null);
  const [outreachTemplateForm, setOutreachTemplateForm] =
    useState<OutreachTemplateFormState>(createEmptyOutreachTemplateForm());
  const [llmForm, setLlmForm] = useState<LLMFormState>(createEmptyLLMForm());
  const [submittingIdentity, setSubmittingIdentity] = useState(false);
  const [loadingIdentityDeletionImpactId, setLoadingIdentityDeletionImpactId] =
    useState<number | null>(null);
  const [identityDeletionImpact, setIdentityDeletionImpact] =
    useState<IdentityDeletionImpactDTO | null>(null);
  const [deletingIdentity, setDeletingIdentity] = useState(false);
  const [savingOutreachTemplate, setSavingOutreachTemplate] = useState(false);
  const [actingOnOutreachTemplate, setActingOnOutreachTemplate] =
    useState(false);
  const [submittingLLM, setSubmittingLLM] = useState(false);
  const [loadingLLMDeletionImpactId, setLoadingLLMDeletionImpactId] = useState<
    number | null
  >(null);
  const [llmDeletionImpact, setLlmDeletionImpact] =
    useState<LLMProfileDeletionImpactDTO | null>(null);
  const [replacementDefaultLLMId, setReplacementDefaultLLMId] = useState<
    number | null
  >(null);
  const [retiringLLM, setRetiringLLM] = useState(false);
  const [importingTemplateFile, setImportingTemplateFile] = useState(false);
  const [testingIdentityConnection, setTestingIdentityConnection] = useState<
    "smtp" | "imap" | null
  >(null);
  const [lastIdentityConnectionResult, setLastIdentityConnectionResult] =
    useState<IdentityConnectionTestSummary | null>(null);
  const [testingLLMConnection, setTestingLLMConnection] = useState(false);
  const [fetchingLLMModels, setFetchingLLMModels] = useState(false);
  const [llmProbeResult, setLlmProbeResult] =
    useState<LLMProfileTestResultDTO | null>(null);
  const [llmModelsResult, setLlmModelsResult] =
    useState<LLMProfileModelsResultDTO | null>(null);
  const [uploadingMaterial, setUploadingMaterial] = useState(false);
  const [actingOnMaterial, setActingOnMaterial] = useState(false);
  const [newMaterialType, setNewMaterialType] =
    useState<IdentityMaterialType>("resume");
  const [materialFilter, setMaterialFilter] =
    useState<MaterialFilterValue>("all");
  const [templateModalOpen, setTemplateModalOpen] = useState(false);
  const [materialModalOpen, setMaterialModalOpen] = useState(false);
  const [highlightedMaterialId, setHighlightedMaterialId] = useState<
    number | null
  >(null);
  const [optimisticMaterial, setOptimisticMaterial] =
    useState<IdentityMaterialDTO | null>(null);
  const [openSetupSections, setOpenSetupSections] = useState<
    Record<ProfileSetupSectionId, boolean>
  >({
    identity: false,
    materials: false,
    model: false,
    test: false,
  });
  const [renderedSetupSections, setRenderedSetupSections] = useState<
    Record<ProfileSetupSectionId, boolean>
  >({
    identity: false,
    materials: false,
    model: false,
    test: false,
  });
  const [testComposeSetupStatus, setTestComposeSetupStatus] =
    useState<TestComposeSetupStatus>("unchecked");
  const identityNameInputRef = useRef<HTMLInputElement | null>(null);
  const llmNameInputRef = useRef<HTMLInputElement | null>(null);
  const templateEditorIdRef = useRef<EditorId>(null);
  const setupSectionRefs = useRef<
    Record<ProfileSetupSectionId, HTMLElement | null>
  >({
    identity: null,
    materials: null,
    model: null,
    test: null,
  });
  const { confirm, choose, dialog: confirmDialog } = useConfirmDialog();

  templateEditorIdRef.current = templateEditorId;
  identityFormRef.current = identityForm;
  identityEditorIdRef.current = identityEditorId;
  selectedIdentityIdRef.current = selectedIdentityId;

  const focusInput = (element: HTMLInputElement | null) => {
    if (!element) {
      return;
    }
    element.focus();
    element.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const getActionErrorMessage = (error: unknown, fallbackMessage: string) =>
    error instanceof Error ? error.message : fallbackMessage;
  const refreshOutreachTemplates = useCallback(async () => {
    setLoadingOutreachTemplates(true);
    try {
      const templates = await listOutreachTemplates(true);
      setOutreachTemplates(templates);
      return templates.filter((template) => !template.archived_at);
    } catch (templateError) {
      notifyError(
        "模板加载失败",
        templateError instanceof Error
          ? templateError.message
          : "加载发信模板失败",
      );
      return [];
    } finally {
      setLoadingOutreachTemplates(false);
    }
  }, [notifyError]);
  const setSetupSectionRef = useCallback(
    (sectionId: ProfileSetupSectionId, element: HTMLElement | null) => {
      setupSectionRefs.current[sectionId] = element;
    },
    [],
  );
  const toggleSetupSection = useCallback((sectionId: ProfileSetupSectionId) => {
    setRenderedSetupSections((previous) => ({
      ...previous,
      [sectionId]: true,
    }));
    setOpenSetupSections((previous) => ({
      ...previous,
      [sectionId]: !previous[sectionId],
    }));
  }, []);
  const openAndScrollToSetupSection = useCallback(
    (sectionId: ProfileSetupSectionId) => {
      setRenderedSetupSections((previous) => ({
        ...previous,
        [sectionId]: true,
      }));
      setOpenSetupSections((previous) => ({
        ...previous,
        [sectionId]: true,
      }));
      window.requestAnimationFrame(() => {
        setupSectionRefs.current[sectionId]?.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      });
    },
    [],
  );
  const handleSetupSectionExitComplete = useCallback(
    (sectionId: ProfileSetupSectionId) => {
      setRenderedSetupSections((previous) => ({
        ...previous,
        [sectionId]: false,
      }));
    },
    [],
  );

  const applyIdentityEditorState = useCallback(
    (nextEditor: IdentityDTO | "new") => {
      const nextForm =
        nextEditor === "new"
          ? createEmptyIdentityForm()
          : toIdentityForm(nextEditor);
      const nextEditorId = nextEditor === "new" ? "new" : nextEditor.id;
      identityFormRef.current = nextForm;
      identityFormBaselineRef.current = nextForm;
      identityEditorIdRef.current = nextEditorId;
      identityEditorSelectionIdRef.current =
        nextEditor === "new" ? selectedIdentityIdRef.current : nextEditor.id;
      setSmtpPasswordVisible(false);
      setIdentityEditorId(nextEditorId);
      setIdentityForm(nextForm);
      setTemplateModalOpen(false);
      setTestingIdentityConnection(null);
      setLastIdentityConnectionResult(null);
      setHighlightedMaterialId(null);
      setOptimisticMaterial(null);
    },
    [],
  );

  const updateIdentityFormState = useCallback(
    (
      updater: (previous: IdentityFormState) => IdentityFormState,
      persisted: boolean,
    ) => {
      if (persisted) {
        identityFormBaselineRef.current = updater(
          identityFormBaselineRef.current,
        );
      }
      setIdentityForm((previous) => {
        const next = updater(previous);
        identityFormRef.current = next;
        return next;
      });
    },
    [],
  );

  useEffect(() => {
    void refreshOutreachTemplates();
  }, [refreshOutreachTemplates]);

  useEffect(() => {
    if (loading) {
      return;
    }
    if (
      identityEditorId === "new" &&
      selectedIdentityId === identityEditorSelectionIdRef.current
    ) {
      return;
    }

    const selectedEditor =
      identities.find((item) => item.id === selectedIdentityId) ?? null;
    if (selectedEditor) {
      if (identityEditorId !== selectedEditor.id) {
        applyIdentityEditorState(selectedEditor);
      }
      return;
    }

    if (
      isExistingEditorId(identityEditorId) &&
      identities.some((item) => item.id === identityEditorId)
    ) {
      return;
    }

    const fallback = identities[0] ?? null;

    if (fallback) {
      applyIdentityEditorState(fallback);
      return;
    }

    applyIdentityEditorState("new");
  }, [
    applyIdentityEditorState,
    identities,
    identityEditorId,
    loading,
    selectedIdentityId,
  ]);

  useEffect(() => {
    if (loading || llmEditorId === "new") {
      return;
    }
    if (
      isExistingEditorId(llmEditorId) &&
      llmProfiles.some((item) => item.id === llmEditorId)
    ) {
      return;
    }

    const fallback =
      llmProfiles.find((item) => item.id === selectedLlmProfileId) ??
      llmProfiles[0] ??
      null;

    if (fallback) {
      setLlmEditorId(fallback.id);
      setLlmForm(toLLMForm(fallback));
      return;
    }

    setLlmEditorId("new");
    setLlmForm(createEmptyLLMForm());
  }, [llmEditorId, llmProfiles, loading, selectedLlmProfileId]);

  const profileModalOpen = materialModalOpen || templateModalOpen;
  useDocumentScrollLock(profileModalOpen);

  useEffect(() => {
    if (!profileModalOpen) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (templateModalOpen) {
          setTemplateModalOpen(false);
          return;
        }
        setMaterialModalOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [materialModalOpen, profileModalOpen, templateModalOpen]);

  const editingIdentity = isExistingEditorId(identityEditorId)
    ? (identities.find((item) => item.id === identityEditorId) ?? null)
    : null;
  const editingLLM = isExistingEditorId(llmEditorId)
    ? (llmProfiles.find((item) => item.id === llmEditorId) ?? null)
    : null;
  const activeOutreachTemplates = useMemo(
    () => outreachTemplates.filter((template) => !template.archived_at),
    [outreachTemplates],
  );
  const archivedOutreachTemplates = useMemo(
    () => outreachTemplates.filter((template) => Boolean(template.archived_at)),
    [outreachTemplates],
  );
  const identityDefaultOutreachTemplate =
    activeOutreachTemplates.find(
      (template) => template.id === identityForm.default_outreach_template_id,
    ) ?? null;
  const globalDefaultOutreachTemplate =
    activeOutreachTemplates.find((template) => template.is_default) ?? null;

  useEffect(() => {
    if (!templateModalOpen || loadingOutreachTemplates) {
      return;
    }
    if (templateEditorId === "new") {
      return;
    }
    if (
      isExistingEditorId(templateEditorId) &&
      activeOutreachTemplates.some(
        (template) => template.id === templateEditorId,
      )
    ) {
      return;
    }

    const fallback =
      activeOutreachTemplates.find(
        (template) => template.id === identityForm.default_outreach_template_id,
      ) ??
      activeOutreachTemplates.find((template) => template.is_default) ??
      activeOutreachTemplates[0] ??
      null;
    if (fallback) {
      setTemplateEditorId(fallback.id);
      setOutreachTemplateForm(toOutreachTemplateForm(fallback));
      return;
    }
    setTemplateEditorId("new");
    setOutreachTemplateForm(createEmptyOutreachTemplateForm());
  }, [
    activeOutreachTemplates,
    identityForm.default_outreach_template_id,
    loadingOutreachTemplates,
    templateEditorId,
    templateModalOpen,
  ]);

  const defaultIdentity = identities.find((item) => item.is_default) ?? null;
  const defaultLLMProfile = llmProfiles.find((item) => item.is_default) ?? null;
  const llmModelsActionState: ActionResultState = llmModelsResult
    ? llmModelsResult.ok
      ? "success"
      : "error"
    : "idle";
  const llmProbeActionState: ActionResultState = llmProbeResult
    ? llmProbeResult.ok
      ? "success"
      : "error"
    : "idle";
  const displayIdentity = useMemo(() => {
    if (
      !editingIdentity ||
      !optimisticMaterial ||
      editingIdentity.materials.some(
        (material) => material.id === optimisticMaterial.id,
      )
    ) {
      return editingIdentity;
    }

    return {
      ...editingIdentity,
      materials: [optimisticMaterial, ...editingIdentity.materials],
      current_primary_material: optimisticMaterial.is_primary
        ? optimisticMaterial
        : editingIdentity.current_primary_material,
      current_primary_material_id: optimisticMaterial.is_primary
        ? optimisticMaterial.id
        : editingIdentity.current_primary_material_id,
    };
  }, [editingIdentity, optimisticMaterial]);
  const setupIdentity =
    displayIdentity ??
    selectedIdentity ??
    defaultIdentity ??
    identities[0] ??
    null;
  const setupLlmProfile =
    selectedLlmProfile ?? defaultLLMProfile ?? llmProfiles[0] ?? null;
  const setupOutreachTemplate =
    activeOutreachTemplates.find(
      (template) => template.id === setupIdentity?.default_outreach_template_id,
    ) ?? globalDefaultOutreachTemplate;
  const setupHasTemplate = setupOutreachTemplate
    ? setupOutreachTemplate.is_ready
    : Boolean(
        setupIdentity?.outreach_template_subject?.trim() &&
          setupIdentity.outreach_template_body_text?.trim(),
      );
  const setupHasMaterial = Boolean(
    setupIdentity?.current_primary_material || setupIdentity?.materials.length,
  );
  const setupItems = useMemo<ProfileSetupItem[]>(() => {
    const hasIdentity = Boolean(setupIdentity);
    const hasLlmProfile = Boolean(setupLlmProfile);
    const materialsCompleted = setupHasTemplate && setupHasMaterial;
    const testComposeCompleted = testComposeSetupStatus === "completed";
    const testComposeStatusDetail =
      testComposeSetupStatus === "loading"
        ? "正在检查测试写信记录"
        : testComposeCompleted
          ? "已发送测试邮件"
          : hasIdentity && hasLlmProfile
            ? "待发送测试邮件确认"
            : "待选择身份和模型";
    const materialStatusDetail = !setupIdentity
      ? "待保存身份后上传材料"
      : materialsCompleted
        ? "默认模板和材料已准备"
        : !setupHasTemplate && !setupHasMaterial
          ? "待填写默认模板并上传材料"
          : !setupHasTemplate
            ? "待填写默认模板"
            : "待上传材料";

    return PROFILE_SETUP_STAGES.map((stage) => {
      if (stage.id === "identity") {
        return {
          ...stage,
          completed: hasIdentity,
          statusDetail: hasIdentity
            ? `已保存身份：${getIdentityProfileName(setupIdentity!)}`
            : "待创建发件身份",
        };
      }
      if (stage.id === "materials") {
        return {
          ...stage,
          completed: materialsCompleted,
          statusDetail: materialStatusDetail,
        };
      }
      if (stage.id === "model") {
        return {
          ...stage,
          completed: hasLlmProfile,
          statusDetail: hasLlmProfile
            ? `已保存模型：${setupLlmProfile!.name}`
            : "待保存模型",
        };
      }
      return {
        ...stage,
        completed: testComposeCompleted,
        statusDetail: testComposeStatusDetail,
      };
    });
  }, [
    setupHasMaterial,
    setupHasTemplate,
    setupIdentity,
    setupLlmProfile,
    testComposeSetupStatus,
  ]);
  const hasResolvedTestComposeSetup =
    selectedIdentityId === null ||
    testComposeSetupStatus === "completed" ||
    testComposeSetupStatus === "pending";
  const shouldShowProfileSetupRecommendations =
    !loadingOutreachTemplates &&
    hasResolvedTestComposeSetup &&
    setupItems.some((item) => !item.completed);

  useEffect(() => {
    if (!selectedIdentityId) {
      setTestComposeSetupStatus("unchecked");
      return;
    }

    let ignore = false;

    const loadTestComposeStatus = async () => {
      setTestComposeSetupStatus("loading");
      try {
        const status = await getTestComposeStatus(selectedIdentityId);
        if (ignore) {
          return;
        }
        setTestComposeSetupStatus(status.completed ? "completed" : "pending");
      } catch {
        if (!ignore) {
          setTestComposeSetupStatus("pending");
        }
      }
    };

    void loadTestComposeStatus();

    return () => {
      ignore = true;
    };
  }, [selectedIdentityId]);

  useEffect(() => {
    if (!editingIdentity) {
      setMaterialModalOpen(false);
    }
  }, [editingIdentity]);

  useEffect(() => {
    if (!editingIdentity || !optimisticMaterial) {
      return;
    }
    if (
      editingIdentity.materials.some(
        (material) => material.id === optimisticMaterial.id,
      )
    ) {
      setOptimisticMaterial(null);
    }
  }, [editingIdentity, optimisticMaterial]);

  useEffect(() => {
    if (!materialModalOpen || highlightedMaterialId === null) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      const element = document.querySelector<HTMLElement>(
        `[data-material-id="${highlightedMaterialId}"]`,
      );
      element?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [displayIdentity, highlightedMaterialId, materialModalOpen]);

  const beginIdentityCreation = async () => {
    if (identityEditorId === "new") {
      return;
    }
    if (
      !(await requestWorkspaceDraftGuard({
        nextIdentityEditorId: "new",
      }))
    ) {
      return;
    }
    applyIdentityEditorState("new");
    window.requestAnimationFrame(() =>
      focusInput(identityNameInputRef.current),
    );
  };

  const beginLLMCreation = () => {
    setLlmEditorId("new");
    setLlmForm(createEmptyLLMForm());
    setLlmProbeResult(null);
    setLlmModelsResult(null);
    setTestingLLMConnection(false);
    setFetchingLLMModels(false);
    window.requestAnimationFrame(() => focusInput(llmNameInputRef.current));
  };

  const openIdentityEditor = async (identityId: number) => {
    const identity = identities.find((item) => item.id === identityId);
    if (!identity) {
      return;
    }
    if (identity.id === identityEditorId) {
      return;
    }
    if (
      !(await requestWorkspaceDraftGuard({
        nextIdentityEditorId: identity.id,
        nextIdentityId: identity.id,
      }))
    ) {
      return;
    }
    if (identity.id === selectedIdentityId) {
      applyIdentityEditorState(identity);
    } else {
      setSelectedIdentityId(identity.id);
    }
  };

  const openLLMEditor = (profileId: number) => {
    const profile = llmProfiles.find((item) => item.id === profileId);
    if (!profile) {
      return;
    }
    setLlmEditorId(profile.id);
    setLlmForm(toLLMForm(profile));
    setLlmProbeResult(null);
    setLlmModelsResult(null);
    setTestingLLMConnection(false);
    setFetchingLLMModels(false);
  };

  const beginOutreachTemplateCreation = () => {
    setTemplateEditorId("new");
    setOutreachTemplateForm(createEmptyOutreachTemplateForm());
  };

  const openOutreachTemplateEditor = (templateId: number) => {
    const template = activeOutreachTemplates.find(
      (item) => item.id === templateId,
    );
    if (!template) {
      return;
    }
    setTemplateEditorId(template.id);
    setOutreachTemplateForm(toOutreachTemplateForm(template));
  };

  const openOutreachTemplateLibrary = () => {
    if (loadingOutreachTemplates) {
      setTemplateEditorId(null);
      setOutreachTemplateForm(createEmptyOutreachTemplateForm());
      setTemplateModalOpen(true);
      return;
    }
    const fallback =
      activeOutreachTemplates.find(
        (template) => template.id === identityForm.default_outreach_template_id,
      ) ??
      activeOutreachTemplates.find((template) => template.is_default) ??
      activeOutreachTemplates[0] ??
      null;
    if (fallback) {
      setTemplateEditorId(fallback.id);
      setOutreachTemplateForm(toOutreachTemplateForm(fallback));
    } else {
      beginOutreachTemplateCreation();
    }
    setTemplateModalOpen(true);
  };

  const handleSmtpHostChange = (nextSmtpHost: string) => {
    setIdentityForm((previous) => ({
      ...previous,
      smtp_host: nextSmtpHost,
      imap_host: shouldSyncImapHost(previous.smtp_host, previous.imap_host)
        ? inferImapHost(nextSmtpHost)
        : previous.imap_host,
    }));
  };

  const runIdentityConnectionTest = async (kind: "smtp" | "imap") => {
    if (!editingIdentity) {
      return;
    }

    setTestingIdentityConnection(kind);
    try {
      const savedIdentity = await saveIdentity({ silent: true });
      if (!savedIdentity) {
        return;
      }
      const result =
        kind === "smtp"
          ? await testIdentitySmtp(savedIdentity.id)
          : await testIdentityImap(savedIdentity.id);
      if (!result.ok) {
        setLastIdentityConnectionResult({
          kind,
          status: "error",
          message: result.message,
          possibleCause: result.possible_cause,
        });
        notifyError(`${kind.toUpperCase()} 连接测试失败`, result.message);
        return;
      }
      setLastIdentityConnectionResult({
        kind,
        status: "success",
        message: result.message,
        possibleCause: null,
      });
      notifySuccess(`${kind.toUpperCase()} 连接测试成功`, result.message);
    } catch (testError) {
      const message = getActionErrorMessage(
        testError,
        `${kind.toUpperCase()} 测试失败`,
      );
      setLastIdentityConnectionResult({
        kind,
        status: "error",
        message,
        possibleCause: null,
      });
      notifyError(`${kind.toUpperCase()} 连接测试失败`, message);
    } finally {
      setTestingIdentityConnection(null);
    }
  };

  const saveOutreachTemplate =
    async (): Promise<OutreachTemplateDTO | null> => {
      if (!desktopBackendReady) {
        notifyError(
          "系统正在准备本地数据",
          "请等待系统准备完成后再保存模板，已填写内容不会丢失。",
        );
        return null;
      }
      if (!outreachTemplateForm.name.trim()) {
        notifyFormErrors("请检查表单", ["请填写模板名称"]);
        return null;
      }

      setSavingOutreachTemplate(true);
      try {
        const payload = toOutreachTemplatePayload(outreachTemplateForm);
        const isCreating = templateEditorId === "new";
        const saved = isExistingEditorId(templateEditorId)
          ? await updateOutreachTemplate(templateEditorId, payload)
          : await createOutreachTemplate(payload);
        setTemplateEditorId(saved.id);
        setOutreachTemplateForm(toOutreachTemplateForm(saved));
        if (identityForm.default_outreach_template_id === saved.id) {
          updateIdentityFormState(
            (previous) => applyOutreachTemplateToIdentityForm(previous, saved),
            Boolean(editingIdentity),
          );
        }
        await Promise.all([refreshOutreachTemplates(), refreshSelections()]);
        notifySuccess(
          isCreating ? "模板创建成功" : "模板保存成功",
          "缺失主题或正文时会标记为“待完善”。",
        );
        return saved;
      } catch (saveError) {
        notifyError(
          "模板保存失败",
          getActionErrorMessage(saveError, "保存发信模板失败"),
        );
        return null;
      } finally {
        setSavingOutreachTemplate(false);
      }
    };

  const handleSetIdentityDefaultTemplate = async (
    template: OutreachTemplateDTO,
  ) => {
    setActingOnOutreachTemplate(true);
    try {
      if (editingIdentity) {
        await updateIdentityDefaultOutreachTemplate(
          editingIdentity.id,
          template.id,
        );
        await refreshSelections();
      }
      updateIdentityFormState(
        (previous) => applyOutreachTemplateToIdentityForm(previous, template),
        Boolean(editingIdentity),
      );
      notifySuccess(
        "身份默认模板已更新",
        editingIdentity
          ? `“${getIdentityProfileName(editingIdentity)}”之后创建的任务将默认选择“${template.name}”。`
          : `保存新身份时会将“${template.name}”设为默认模板。`,
      );
    } catch (templateError) {
      notifyError(
        "设置身份默认模板失败",
        getActionErrorMessage(templateError, "设置身份默认模板失败"),
      );
    } finally {
      setActingOnOutreachTemplate(false);
    }
  };

  const handleSetGlobalDefaultTemplate = async (templateId: number) => {
    setActingOnOutreachTemplate(true);
    try {
      const saved = await setGlobalDefaultOutreachTemplate(templateId);
      setOutreachTemplateForm((previous) => ({
        ...previous,
        is_default: templateEditorId === saved.id,
      }));
      await refreshOutreachTemplates();
      notifySuccess(
        "全局默认模板已更新",
        `未设置身份默认模板时，将优先选择“${saved.name}”。`,
      );
    } catch (templateError) {
      notifyError(
        "设置全局默认模板失败",
        getActionErrorMessage(templateError, "设置全局默认模板失败"),
      );
    } finally {
      setActingOnOutreachTemplate(false);
    }
  };

  const handleClearIdentityDefaultTemplate = async () => {
    setActingOnOutreachTemplate(true);
    try {
      if (editingIdentity) {
        await updateIdentityDefaultOutreachTemplate(editingIdentity.id, null);
        await refreshSelections();
      }
      updateIdentityFormState(
        clearOutreachTemplateFromIdentityForm,
        Boolean(editingIdentity),
      );
      notifySuccess(
        "身份默认模板已取消",
        editingIdentity
          ? `“${getIdentityProfileName(editingIdentity)}”之后创建的任务将使用全局默认模板（如有）。`
          : "保存新身份后，将使用全局默认模板（如有）。",
      );
    } catch (templateError) {
      notifyError(
        "取消身份默认模板失败",
        getActionErrorMessage(templateError, "取消身份默认模板失败"),
      );
    } finally {
      setActingOnOutreachTemplate(false);
    }
  };

  const handleDuplicateOutreachTemplate = async (templateId: number) => {
    setActingOnOutreachTemplate(true);
    try {
      const duplicate = await duplicateOutreachTemplate(templateId);
      await refreshOutreachTemplates();
      setTemplateEditorId(duplicate.id);
      setOutreachTemplateForm(toOutreachTemplateForm(duplicate));
      notifySuccess("模板复制成功", `已创建“${duplicate.name}”。`);
    } catch (templateError) {
      notifyError(
        "复制模板失败",
        getActionErrorMessage(templateError, "复制发信模板失败"),
      );
    } finally {
      setActingOnOutreachTemplate(false);
    }
  };

  const handleDeleteOutreachTemplate = async (
    template: OutreachTemplateDTO,
  ) => {
    const confirmed = await confirm({
      title: `归档模板“${template.name}”？`,
      description:
        "归档后会取消默认关联并停止用于新任务；已创建任务和模板内容会保留，可稍后恢复。",
      confirmLabel: "确认归档",
      cancelLabel: "取消",
      tone: "danger",
    });
    if (!confirmed) {
      return;
    }

    setActingOnOutreachTemplate(true);
    try {
      const wasIdentityDefault =
        identityForm.default_outreach_template_id === template.id;
      await archiveOutreachTemplate(template.id);
      if (wasIdentityDefault) {
        updateIdentityFormState(
          clearOutreachTemplateFromIdentityForm,
          Boolean(editingIdentity),
        );
      }
      const [remainingTemplates] = await Promise.all([
        refreshOutreachTemplates(),
        refreshSelections(),
      ]);
      const fallback =
        (!wasIdentityDefault
          ? remainingTemplates.find(
              (item) => item.id === identityForm.default_outreach_template_id,
            )
          : null) ??
        remainingTemplates.find((item) => item.is_default) ??
        remainingTemplates[0] ??
        null;
      if (fallback) {
        setTemplateEditorId(fallback.id);
        setOutreachTemplateForm(toOutreachTemplateForm(fallback));
      } else {
        beginOutreachTemplateCreation();
      }
      notifySuccess("模板已归档", "已创建任务不受影响，可在模板库中恢复。");
    } catch (templateError) {
      notifyError(
        "归档模板失败",
        getActionErrorMessage(templateError, "归档发信模板失败"),
      );
    } finally {
      setActingOnOutreachTemplate(false);
    }
  };

  const handleRestoreOutreachTemplate = async (
    template: OutreachTemplateDTO,
  ) => {
    setActingOnOutreachTemplate(true);
    try {
      const restored = await restoreOutreachTemplate(template.id);
      await refreshOutreachTemplates();
      setTemplateEditorId(restored.id);
      setOutreachTemplateForm(toOutreachTemplateForm(restored));
      notifySuccess("模板已恢复", `“${restored.name}”已回到可用模板列表。`);
    } catch (templateError) {
      notifyError(
        "恢复模板失败",
        getActionErrorMessage(templateError, "恢复发信模板失败"),
      );
    } finally {
      setActingOnOutreachTemplate(false);
    }
  };

  const handleTemplateFileImport = async (file: File) => {
    if (importingTemplateFile) {
      return;
    }

    const importTargetEditorId = templateEditorId;
    const hasExistingTemplateBody =
      hasVisibleTemplateBody(outreachTemplateForm);

    if (hasExistingTemplateBody) {
      const shouldReplaceTemplateBody = await confirm({
        title: "确认覆盖当前模板正文？",
        description: "导入模板文件会替换当前正文内容，主题不会被修改。",
        confirmLabel: "覆盖并导入",
        cancelLabel: "取消",
        tone: "danger",
      });

      if (!shouldReplaceTemplateBody) {
        return;
      }
    }

    setImportingTemplateFile(true);
    try {
      const imported = await importIdentityTemplate(file);
      if (templateEditorIdRef.current !== importTargetEditorId) {
        return;
      }

      setOutreachTemplateForm((previous) => ({
        ...previous,
        name: previous.name.trim() || file.name.replace(/\.[^.]+$/, "").trim(),
        outreach_template_body_text: imported.body_text,
        outreach_template_body_html: imported.body_html,
      }));
      notifySuccess(
        "模板导入成功",
        `已导入 ${imported.format_name} 并生成纯文本正文。`,
      );
    } catch (importError) {
      notifyError(
        "模板导入失败",
        getActionErrorMessage(importError, "导入模板文件失败"),
      );
    } finally {
      setImportingTemplateFile(false);
    }
  };

  const runLlmConnectionTest = async () => {
    if (!editingLLM) {
      return;
    }
    if (!desktopBackendReady) {
      notifyError(
        "系统正在准备本地数据",
        desktopDisableReason ?? "请等待系统准备完成后再测试模型。",
      );
      return;
    }

    setTestingLLMConnection(true);
    setLlmProbeResult(null);
    const testedProfileId = editingLLM.id;
    const testedPayload = toLLMPayload(llmForm);
    try {
      const result = await testLLMProfilePreview(testedPayload);
      if (!result.ok) {
        setLlmProbeResult(result);
        return;
      }

      try {
        await updateLLMProfile(testedProfileId, testedPayload);
        await refreshSelections();
        setLlmProbeResult(result);
      } catch (saveError) {
        const message = getActionErrorMessage(
          saveError,
          "模型配置自动保存失败",
        );
        setLlmProbeResult({
          ...result,
          ok: false,
          message: `模型测试成功，但配置自动保存失败：${message}`,
        });
        notifyError("模型配置自动保存失败", message);
      }
    } catch (testError) {
      setLlmProbeResult({
        ok: false,
        message:
          testError instanceof Error ? testError.message : "连接测试失败",
        resolved_base_url: null,
        request_url: null,
        attempted_urls: [],
        endpoint_kind: null,
        status_code: null,
        duration_ms: null,
        consumes_tokens: true,
        prompt_tokens: null,
        completion_tokens: null,
        total_tokens: null,
        response_preview: null,
      });
    } finally {
      setTestingLLMConnection(false);
    }
  };

  const runLlmModelsFetch = async () => {
    if (!editingLLM) {
      return;
    }
    if (!desktopBackendReady) {
      notifyError(
        "系统正在准备本地数据",
        desktopDisableReason ?? "请等待系统准备完成后再获取模型列表。",
      );
      return;
    }

    setFetchingLLMModels(true);
    setLlmModelsResult(null);
    try {
      const result = await fetchLLMProfileModelsPreview(toLLMPayload(llmForm));
      setLlmModelsResult(result);
    } catch (testError) {
      setLlmModelsResult({
        ok: false,
        message:
          testError instanceof Error ? testError.message : "获取模型列表失败",
        resolved_base_url: null,
        request_url: null,
        attempted_urls: [],
        endpoint_kind: null,
        status_code: null,
        duration_ms: null,
        consumes_tokens: false,
        models: [],
        selected_model_available: null,
      });
    } finally {
      setFetchingLLMModels(false);
    }
  };

  const handleSelectSuggestedModel = (modelName: string) => {
    setLlmForm((previous) => ({
      ...previous,
      model_name: modelName,
    }));
  };

  const saveIdentity = async ({
    silent = false,
  }: { silent?: boolean } = {}): Promise<IdentityDTO | null> => {
    if (!desktopBackendReady) {
      notifyError(
        "系统正在准备本地数据",
        "这不是身份配置错误。请等待系统准备完成后再保存，已填写内容不会丢失。",
      );
      return null;
    }

    if (!identityForm.profile_name.trim() || !identityForm.sender_name.trim()) {
      notifyFormErrors("请检查表单", ["请填写身份名称和发件人姓名"]);
      return null;
    }
    if (
      !identityForm.email_address.trim() ||
      !identityForm.smtp_host.trim() ||
      !identityForm.smtp_password.trim() ||
      !identityForm.imap_host.trim() ||
      !identityForm.imap_port.trim()
    ) {
      notifyFormErrors("请检查表单", ["请先填写所有带红色星号的身份必填项"]);
      return null;
    }
    const isCreatingIdentity = identityEditorId === "new";
    setSubmittingIdentity(true);
    try {
      const payload = toIdentityPayload(identityForm);
      const saved = isExistingEditorId(identityEditorId)
        ? await updateIdentity(identityEditorId, payload)
        : await createIdentity(payload);
      await refreshSelections();
      if (isCreatingIdentity) {
        setSelectedIdentityId(saved.id);
      }
      applyIdentityEditorState(saved);
      if (!silent) {
        notifySuccess(identityEditorId === "new" ? "身份已创建" : "身份已保存");
      }
      return saved;
    } catch (saveError) {
      notifyError(
        "身份保存失败",
        getActionErrorMessage(saveError, "身份保存失败"),
      );
      return null;
    } finally {
      setSubmittingIdentity(false);
    }
  };

  const openIdentityDeletionImpact = async (identityId: number) => {
    setLoadingIdentityDeletionImpactId(identityId);
    try {
      setIdentityDeletionImpact(await getIdentityDeletionImpact(identityId));
    } catch (impactError) {
      notifyError(
        "无法检查删除影响",
        getActionErrorMessage(impactError, "读取身份关联数据失败"),
      );
    } finally {
      setLoadingIdentityDeletionImpactId(null);
    }
  };

  const deleteSelectedIdentity = async () => {
    const impact = identityDeletionImpact;
    if (!impact?.can_delete || deletingIdentity) {
      return;
    }
    setDeletingIdentity(true);
    try {
      await deleteIdentity(impact.identity_id, impact.revision);
      await refreshSelections();
      const emptyForm = createEmptyIdentityForm();
      identityEditorIdRef.current = null;
      identityFormRef.current = emptyForm;
      identityFormBaselineRef.current = emptyForm;
      setIdentityEditorId(null);
      setIdentityForm(emptyForm);
      setSmtpPasswordVisible(false);
      setIdentityDeletionImpact(null);
      notifySuccess(
        `已删除身份配置“${impact.identity_name}”`,
        "邮箱密码已清除，历史任务、通信记录和材料均已保留。",
      );
    } catch (deleteError) {
      if (deleteError instanceof ApiError) {
        const updatedImpact =
          typeof deleteError.details === "object" &&
          deleteError.details !== null &&
          "impact" in deleteError.details
            ? deleteError.details.impact
            : null;
        if (isIdentityDeletionImpact(updatedImpact)) {
          setIdentityDeletionImpact(updatedImpact);
        }
      }
      notifyError(
        deleteError instanceof ApiError &&
          deleteError.code === "IDENTITY_DELETE_PLAN_STALE"
          ? "关联状态已变化"
          : "删除身份配置失败",
        getActionErrorMessage(deleteError, "删除身份配置失败"),
      );
    } finally {
      setDeletingIdentity(false);
    }
  };

  saveIdentityRef.current = saveIdentity;

  useEffect(() => {
    return registerWorkspaceDraftGuard(async (request) => {
      if (
        request?.nextLlmProfileId !== undefined &&
        request.nextIdentityId === undefined &&
        request.nextIdentityEditorId === undefined &&
        request.nextPath === undefined
      ) {
        return true;
      }

      const nextEditorId =
        request?.nextIdentityEditorId ?? request?.nextIdentityId;
      if (
        nextEditorId !== undefined &&
        nextEditorId === identityEditorIdRef.current &&
        request?.nextPath === undefined
      ) {
        return true;
      }
      if (
        areIdentityFormsEqual(
          identityFormRef.current,
          identityFormBaselineRef.current,
        )
      ) {
        return true;
      }
      if (submittingIdentity || testingIdentityConnection !== null) {
        notifyError(
          "身份配置正在处理",
          "请等待当前保存或连接测试完成后再切换。",
        );
        return false;
      }

      const action = await choose({
        title: "保存身份修改？",
        description: "切换后，未保存的身份配置修改将丢失。",
        confirmLabel: "保存并切换",
        secondaryLabel: "不保存切换",
        cancelLabel: "继续编辑",
      });
      if (action === "cancel") {
        return false;
      }
      if (action === "secondary") {
        return true;
      }

      const saved = await saveIdentityRef.current({ silent: true });
      if (!saved) {
        return false;
      }
      notifySuccess("身份配置已保存");
      return true;
    });
  }, [
    choose,
    notifyError,
    notifySuccess,
    registerWorkspaceDraftGuard,
    submittingIdentity,
    testingIdentityConnection,
  ]);

  const saveLLM = async () => {
    if (
      !llmForm.name.trim() ||
      !llmForm.api_base_url.trim() ||
      !llmForm.api_key.trim() ||
      !llmForm.model_name.trim()
    ) {
      notifyFormErrors("请检查表单", ["请先填写所有带红色星号的模型必填项"]);
      return;
    }

    setSubmittingLLM(true);
    try {
      const wasCreating = llmEditorId === "new";
      const payload = toLLMPayload(llmForm);
      const saved = isExistingEditorId(llmEditorId)
        ? await updateLLMProfile(llmEditorId, payload)
        : await createLLMProfile(payload);
      if (wasCreating) {
        setSelectedLlmProfileId(saved.id);
      }
      await refreshSelections();
      setLlmEditorId(saved.id);
      setLlmForm(toLLMForm(saved));
      notifySuccess(wasCreating ? "模型配置已创建" : "模型配置已保存");
    } catch (saveError) {
      notifyError(
        "模型保存失败",
        getActionErrorMessage(saveError, "模型配置保存失败"),
      );
    } finally {
      setSubmittingLLM(false);
    }
  };

  const openLLMDeletionImpact = async (profileId: number) => {
    setLoadingLLMDeletionImpactId(profileId);
    try {
      const impact = await getLLMProfileDeletionImpact(profileId);
      setReplacementDefaultLLMId(null);
      setLlmDeletionImpact(impact);
    } catch (impactError) {
      notifyError(
        "无法检查删除影响",
        getActionErrorMessage(impactError, "读取模型配置关联数据失败"),
      );
    } finally {
      setLoadingLLMDeletionImpactId(null);
    }
  };

  const retireSelectedLLM = async () => {
    const impact = llmDeletionImpact;
    if (!impact?.can_delete || retiringLLM) {
      return;
    }
    setRetiringLLM(true);
    try {
      const result = await deleteLLMProfile(
        impact.profile_id,
        impact.revision,
        impact.is_default ? replacementDefaultLLMId : null,
      );
      if (selectedLlmProfileId === impact.profile_id) {
        const nextProfileId =
          result.default_profile_id ??
          replacementDefaultLLMId ??
          llmProfiles.find((profile) => profile.id !== impact.profile_id)?.id ??
          null;
        setSelectedLlmProfileId(nextProfileId);
      }
      await refreshSelections();
      setLlmDeletionImpact(null);
      setReplacementDefaultLLMId(null);
      setLlmEditorId(null);
      setLlmForm(createEmptyLLMForm());
      const preservedCount = Object.values(result.references_preserved).reduce(
        (total, count) => total + count,
        0,
      );
      notifySuccess(
        `已删除模型配置“${result.profile_name}”`,
        preservedCount > 0
          ? `API Key 已清除，${preservedCount} 条关联记录已保留。`
          : "API Key 已清除，历史数据未受影响。",
      );
    } catch (deleteError) {
      if (deleteError instanceof ApiError) {
        const updatedImpact =
          typeof deleteError.details === "object" &&
          deleteError.details !== null &&
          "impact" in deleteError.details
            ? deleteError.details.impact
            : null;
        if (isDeletionImpact(updatedImpact)) {
          setLlmDeletionImpact(updatedImpact);
          setReplacementDefaultLLMId(null);
        }
      }
      notifyError(
        deleteError instanceof ApiError &&
          deleteError.code === "LLM_PROFILE_DELETE_PLAN_STALE"
          ? "关联状态已变化"
          : "删除模型配置失败",
        getActionErrorMessage(deleteError, "删除模型配置失败"),
      );
    } finally {
      setRetiringLLM(false);
    }
  };

  const handleOpenMaterial = async (material: IdentityMaterialDTO) => {
    if (!isDesktopApp()) {
      notifyError(
        "无法打开材料",
        "请在桌面应用中打开材料，或使用下载按钮保存后查看。",
      );
      return;
    }

    const result = await openDesktopMaterial(material.id);
    if (!result.ok) {
      notifyError("无法打开材料", result.message);
    }
  };

  const handleDownloadMaterial = async (material: IdentityMaterialDTO) => {
    try {
      await downloadMaterial(material.id, material.original_filename);
    } catch (downloadError) {
      notifyError(
        "下载材料失败",
        getActionErrorMessage(downloadError, "下载材料失败"),
      );
    }
  };

  const handleMaterialUpload = async (file: File) => {
    if (!editingIdentity) {
      return;
    }
    setUploadingMaterial(true);
    try {
      const uploadedMaterial = await uploadIdentityMaterial(
        editingIdentity.id,
        {
          file,
          materialType: newMaterialType,
        },
      );
      setOptimisticMaterial(uploadedMaterial);
      setMaterialFilter(uploadedMaterial.material_type);
      setHighlightedMaterialId(uploadedMaterial.id);
      await refreshSelections();
      notifySuccess(
        "材料上传成功",
        `已上传为${getMaterialTypeLabel(uploadedMaterial.material_type)}：${uploadedMaterial.display_name}`,
      );
    } catch (uploadError) {
      notifyError(
        "材料上传失败",
        getActionErrorMessage(uploadError, "材料上传失败"),
      );
    } finally {
      setUploadingMaterial(false);
    }
  };

  const handleSetPrimaryMaterial = async (material: IdentityMaterialDTO) => {
    if (!editingIdentity) {
      return;
    }
    setActingOnMaterial(true);
    try {
      await setPrimaryMaterial(editingIdentity.id, material.id);
      await refreshSelections();
      notifySuccess(
        "设为默认材料成功",
        `已将“${material.display_name}”设为“${getIdentityProfileName(editingIdentity)}”的默认材料。`,
      );
      setHighlightedMaterialId(material.id);
    } catch (materialError) {
      notifyError(
        "设为默认材料失败",
        getActionErrorMessage(materialError, "设置默认材料失败"),
      );
    } finally {
      setActingOnMaterial(false);
    }
  };

  const handleDeleteMaterial = async (material: IdentityMaterialDTO) => {
    setActingOnMaterial(true);
    try {
      const impact = await getMaterialDeletionImpact(material.id);
      const confirmed = await confirm({
        title: `永久删除材料“${material.display_name}”？`,
        description: buildMaterialDeletionConfirmationDescription(impact),
        confirmLabel: "确认永久删除",
        cancelLabel: "先保留",
        tone: "danger",
      });
      if (!confirmed) {
        return;
      }
      await deleteMaterial(material.id, impact.deletion_fingerprint);
      await refreshSelections();
      notifySuccess(
        "删除材料成功",
        material.is_primary
          ? `材料“${material.display_name}”已删除，当前未设默认材料。`
          : `材料“${material.display_name}”已删除。`,
      );
      if (optimisticMaterial?.id === material.id) {
        setOptimisticMaterial(null);
      }
      if (highlightedMaterialId === material.id) {
        setHighlightedMaterialId(null);
      }
    } catch (materialError) {
      notifyError(
        "删除材料失败",
        getActionErrorMessage(materialError, "删除材料失败"),
      );
    } finally {
      setActingOnMaterial(false);
    }
  };

  const identityActionButtons = (
    <div className="mt-6 flex flex-wrap gap-3">
      <button
        type="button"
        onClick={() => void saveIdentity()}
        disabled={submittingIdentity || !desktopBackendReady}
        className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
      >
        {submittingIdentity && <Loader2 className="h-4 w-4 animate-spin" />}
        {!desktopBackendReady
          ? (desktopDisableReason ?? "系统准备中")
          : "保存身份"}
      </button>
      {!desktopBackendReady && (
        <p className="basis-full text-xs text-amber-700">
          本地数据准备完成后即可继续操作，已填写内容不会丢失。
        </p>
      )}
      {editingIdentity && (
        <>
          {selectedIdentityId === editingIdentity.id ? (
            <span className="inline-flex items-center rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-700">
              当前使用中
            </span>
          ) : (
            <button
              type="button"
              onClick={() => {
                void (async () => {
                  if (
                    !(await requestWorkspaceDraftGuard({
                      nextIdentityId: editingIdentity.id,
                    }))
                  ) {
                    return;
                  }
                  setSelectedIdentityId(editingIdentity.id);
                  notifySuccess(
                    `当前身份：${getIdentityProfileName(editingIdentity)}`,
                  );
                })();
              }}
              className="ui-btn-secondary"
            >
              设为当前
            </button>
          )}
          {editingIdentity.is_default ? (
            <span className="inline-flex items-center rounded-xl border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-700">
              已设为默认
            </span>
          ) : (
            <button
              type="button"
              onClick={() => {
                void setDefaultIdentity(editingIdentity.id)
                  .then(async () => {
                    await refreshSelections();
                    updateIdentityFormState(
                      (previous) => ({
                        ...previous,
                        is_default: true,
                      }),
                      true,
                    );
                    notifySuccess(
                      `默认身份：${getIdentityProfileName(editingIdentity)}`,
                    );
                  })
                  .catch((defaultError) => {
                    notifyError(
                      "设为默认身份失败",
                      getActionErrorMessage(defaultError, "设置默认身份失败"),
                    );
                  });
              }}
              className="ui-btn-secondary"
            >
              设为默认
            </button>
          )}
          <button
            type="button"
            disabled={loadingIdentityDeletionImpactId === editingIdentity.id}
            onClick={() => void openIdentityDeletionImpact(editingIdentity.id)}
            className="ui-btn-danger"
          >
            {loadingIdentityDeletionImpactId === editingIdentity.id
              ? "检查关联数据…"
              : "删除"}
          </button>
        </>
      )}
    </div>
  );
  const setIdentitySetupSectionRef = useCallback(
    (element: HTMLElement | null) => setSetupSectionRef("identity", element),
    [setSetupSectionRef],
  );
  const setMaterialsSetupSectionRef = useCallback(
    (element: HTMLElement | null) => setSetupSectionRef("materials", element),
    [setSetupSectionRef],
  );
  const setModelSetupSectionRef = useCallback(
    (element: HTMLElement | null) => setSetupSectionRef("model", element),
    [setSetupSectionRef],
  );
  const setTestSetupSectionRef = useCallback(
    (element: HTMLElement | null) => setSetupSectionRef("test", element),
    [setSetupSectionRef],
  );

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      <div className="rounded-3xl border border-stone-200 bg-[#fcfbf8] p-6 shadow-sm">
        <h1 className="text-3xl font-semibold text-stone-900">个人中心</h1>
        <div className="mt-4 flex flex-wrap gap-3 text-xs text-stone-600">
          <span className="rounded-full border border-stone-200 bg-white px-3 py-1.5">
            身份：
            {selectedIdentity
              ? getIdentityProfileName(selectedIdentity)
              : "未选择"}
          </span>
          <span className="rounded-full border border-stone-200 bg-white px-3 py-1.5">
            模型：{selectedLlmProfile?.name ?? "未选择"}
          </span>
        </div>
      </div>

      {loading ? (
        <div className="mt-6 flex items-center justify-center gap-2 rounded-3xl border border-stone-200 bg-white px-6 py-14 text-sm text-stone-500 shadow-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载配置…
        </div>
      ) : (
        <div className="mt-6 space-y-6">
          {shouldShowProfileSetupRecommendations ? (
            <section className="rounded-3xl border border-stone-200 bg-[linear-gradient(135deg,rgba(248,244,236,0.95),rgba(255,255,255,0.98))] p-6 shadow-sm">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <h2 className="text-xl font-semibold text-stone-900">
                    首次配置
                  </h2>
                  <p className="mt-2 text-sm leading-6 text-stone-600">
                    完成以下 4 项即可开始使用。
                  </p>
                </div>
                <ContextualHelpLink
                  href={PROFILE_HELP_LINKS.firstRun}
                  tone="surface"
                >
                  查看完整配置教程
                </ContextualHelpLink>
              </div>
              <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {setupItems.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => openAndScrollToSetupSection(item.id)}
                    className={clsx(
                      "rounded-2xl border bg-white px-4 py-3 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md focus:outline-none focus:ring-2 focus:ring-primary/20",
                      item.completed
                        ? "border-emerald-200"
                        : "border-amber-200",
                    )}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm font-semibold text-stone-900">
                        {item.label}
                      </span>
                      <span
                        className={clsx(
                          "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium",
                          item.completed
                            ? "bg-emerald-100 text-emerald-700"
                            : "bg-amber-100 text-amber-700",
                        )}
                      >
                        {item.completed ? (
                          <CheckCircle2 className="h-3.5 w-3.5" />
                        ) : (
                          <XCircle className="h-3.5 w-3.5" />
                        )}
                        {item.completed ? "已完成" : "待完成"}
                      </span>
                    </div>
                    <div className="mt-2 text-xs leading-5 text-stone-500">
                      {item.statusDetail}
                    </div>
                  </button>
                ))}
              </div>
            </section>
          ) : null}

          <ProfileSetupSection
            sectionId="identity"
            title="发件身份"
            description="管理发件邮箱与收发设置。"
            open={openSetupSections.identity}
            renderContent={renderedSetupSections.identity}
            onToggle={() => toggleSetupSection("identity")}
            onExitComplete={() => handleSetupSectionExitComplete("identity")}
            sectionRef={setIdentitySetupSectionRef}
            badge={
              <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-600">
                默认身份：
                {defaultIdentity
                  ? getIdentityProfileName(defaultIdentity)
                  : "未设置"}
              </span>
            }
            helpAction={
              <ContextualHelpLink
                href={PROFILE_HELP_LINKS.mailAuthorization}
                compact
              >
                邮箱配置教程
              </ContextualHelpLink>
            }
          >
            <div className="mt-5 rounded-3xl border border-stone-200 bg-[#fcfbf8] p-4">
              <EditorSwitcher
                label={
                  editingIdentity
                    ? `编辑发件身份：${getIdentityProfileName(editingIdentity)}`
                    : "新建发件身份"
                }
                helper={
                  identities.length > 0 ? "点选切换，或新建一套。" : undefined
                }
                options={identities.map((identity) => ({
                  ...identity,
                  name: getIdentityProfileName(identity),
                }))}
                activeId={identityEditorId}
                createLabel="新建发件身份"
                creatingLabel="新建发件身份"
                onCreate={() => void beginIdentityCreation()}
                onSelect={(identityId) => void openIdentityEditor(identityId)}
              />
            </div>

            <div className="mt-6 grid gap-4 md:grid-cols-2">
              <label className="block">
                {<FieldLabel label={"身份名称"} required={true} />}
                <input
                  ref={identityNameInputRef}
                  aria-label="身份名称"
                  value={identityForm.profile_name}
                  onChange={(event) =>
                    setIdentityForm((previous) => ({
                      ...previous,
                      name: event.target.value,
                      profile_name: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：博士申请邮箱"
                />
              </label>
              <label className="block">
                {<FieldLabel label={"发件人姓名"} required={true} />}
                <input
                  aria-label="发件人姓名"
                  value={identityForm.sender_name}
                  onChange={(event) =>
                    setIdentityForm((previous) => ({
                      ...previous,
                      sender_name: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：张三"
                />
              </label>
              <label className="block">
                {<FieldLabel label={"发件邮箱"} required={true} />}
                <input
                  value={identityForm.email_address}
                  onChange={(event) =>
                    setIdentityForm((previous) => ({
                      ...previous,
                      email_address: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：your.name@example.com"
                />
              </label>
              <label className="block">
                {<FieldLabel label={"SMTP 服务器"} required={true} />}
                <input
                  value={identityForm.smtp_host}
                  onChange={(event) => handleSmtpHostChange(event.target.value)}
                  className={inputClassName}
                  placeholder="示例：smtp.163.com"
                />
              </label>
              <div className="block">
                <FormFieldHeader
                  id="smtp-password"
                  label="邮箱授权码"
                  required
                  help={
                    <ContextualHelpLink
                      href={PROFILE_HELP_LINKS.mailAuthorization}
                      compact
                    >
                      如何获取授权码
                    </ContextualHelpLink>
                  }
                />
                <div className="group relative">
                  <input
                    id="smtp-password"
                    type={smtpPasswordVisible ? "text" : "password"}
                    value={identityForm.smtp_password}
                    onChange={(event) =>
                      setIdentityForm((previous) => ({
                        ...previous,
                        smtp_password: event.target.value,
                      }))
                    }
                    className={clsx(inputClassName, "pr-11")}
                    placeholder="授权码或应用专用密码"
                  />
                  <button
                    type="button"
                    aria-label={
                      smtpPasswordVisible ? "隐藏授权码" : "显示授权码"
                    }
                    aria-pressed={smtpPasswordVisible}
                    title={smtpPasswordVisible ? "隐藏授权码" : "显示授权码"}
                    onClick={() =>
                      setSmtpPasswordVisible((visible) => !visible)
                    }
                    className="pointer-events-none absolute inset-y-0 right-2 my-auto flex h-7 w-7 items-center justify-center rounded-lg text-stone-400 opacity-0 transition hover:bg-stone-100 hover:text-stone-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100"
                  >
                    {smtpPasswordVisible ? (
                      <EyeOff aria-hidden="true" className="h-4 w-4" />
                    ) : (
                      <Eye aria-hidden="true" className="h-4 w-4" />
                    )}
                  </button>
                </div>
                <p className="mt-2 text-xs leading-5 text-stone-500">
                  不是邮箱登录密码，请填写授权码或应用专用密码。
                </p>
              </div>
              <label className="block">
                {<FieldLabel label={"SMTP 端口"} required={true} />}
                <input
                  type="number"
                  value={identityForm.smtp_port}
                  onChange={(event) =>
                    setIdentityForm((previous) => ({
                      ...previous,
                      smtp_port: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：465"
                />
              </label>
              <label className="block">
                {<FieldLabel label={"IMAP 服务器"} required={true} />}
                <input
                  value={identityForm.imap_host}
                  onChange={(event) =>
                    setIdentityForm((previous) => ({
                      ...previous,
                      imap_host: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：imap.163.com"
                />
              </label>
              <label className="block">
                {<FieldLabel label={"IMAP 端口"} required={true} />}
                <input
                  type="number"
                  value={identityForm.imap_port}
                  onChange={(event) =>
                    setIdentityForm((previous) => ({
                      ...previous,
                      imap_port: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：993"
                />
              </label>
            </div>

            {identityActionButtons}

            {editingIdentity ? (
              <div className="mt-6">
                <IdentityConnectionCard
                  testingIdentityConnection={testingIdentityConnection}
                  lastResult={lastIdentityConnectionResult}
                  onTestSmtp={() => void runIdentityConnectionTest("smtp")}
                  onTestImap={() => void runIdentityConnectionTest("imap")}
                />
              </div>
            ) : null}
          </ProfileSetupSection>

          <ProfileSetupSection
            sectionId="materials"
            title="材料与模板"
            description="准备匹配材料和发信模板。"
            open={openSetupSections.materials}
            renderContent={renderedSetupSections.materials}
            onToggle={() => toggleSetupSection("materials")}
            onExitComplete={() => handleSetupSectionExitComplete("materials")}
            sectionRef={setMaterialsSetupSectionRef}
            badge={
              <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-600">
                管理材料和模板
              </span>
            }
          >
            <div className="mt-6">
              <OutreachTemplateSummaryCard
                form={identityForm}
                template={identityDefaultOutreachTemplate}
                globalTemplate={globalDefaultOutreachTemplate}
                templateCount={activeOutreachTemplates.length}
                loadingTemplates={loadingOutreachTemplates}
                onOpen={openOutreachTemplateLibrary}
              />
            </div>

            {editingIdentity && (
              <div className="mt-6">
                <MaterialSummaryCard
                  identity={displayIdentity ?? editingIdentity}
                  onOpen={() => {
                    setMaterialFilter("all");
                    setHighlightedMaterialId(null);
                    setMaterialModalOpen(true);
                  }}
                />
              </div>
            )}
            {!editingIdentity ? (
              <div className="mt-6 rounded-2xl border border-dashed border-stone-200 bg-stone-50/80 px-4 py-4 text-sm leading-6 text-stone-500">
                创建并保存发件身份后，可上传材料。
              </div>
            ) : null}
          </ProfileSetupSection>

          <ProfileSetupSection
            sectionId="model"
            title="模型配置"
            description="连接并测试用于写信的 AI 模型。"
            open={openSetupSections.model}
            renderContent={renderedSetupSections.model}
            onToggle={() => toggleSetupSection("model")}
            onExitComplete={() => handleSetupSectionExitComplete("model")}
            sectionRef={setModelSetupSectionRef}
            badge={
              <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-600">
                默认模型：{defaultLLMProfile?.name ?? "未设置"}
              </span>
            }
            helpAction={
              <ContextualHelpLink
                href={PROFILE_HELP_LINKS.llmConfiguration}
                compact
              >
                模型配置教程
              </ContextualHelpLink>
            }
          >
            <div className="mt-5 rounded-3xl border border-stone-200 bg-[#fcfbf8] p-4">
              <EditorSwitcher
                label={editingLLM ? `编辑模型：${editingLLM.name}` : "新建模型"}
                helper={
                  llmProfiles.length > 0 ? "点选切换，或新建一套。" : undefined
                }
                options={llmProfiles}
                activeId={llmEditorId}
                createLabel="新建模型"
                creatingLabel="新建模型"
                onCreate={beginLLMCreation}
                onSelect={openLLMEditor}
              />
              <div className="mt-3 flex flex-wrap gap-2 text-xs text-stone-500">
                <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
                  DeepSeek 示例
                </span>
                <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
                  随机性（Temperature）{DEFAULT_LLM_TEMPERATURE}
                </span>
                <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
                  草稿上限 {DEFAULT_LLM_MAX_TOKENS} Token
                </span>
              </div>
            </div>

            <div className="mt-6 grid gap-4 md:grid-cols-2">
              <label className="block">
                {<FieldLabel label={"名称"} required={true} />}
                <input
                  ref={llmNameInputRef}
                  value={llmForm.name}
                  onChange={(event) =>
                    setLlmForm((previous) => ({
                      ...previous,
                      name: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：DeepSeek V4 Flash"
                />
              </label>
              <div className="block md:col-span-2">
                <FormFieldHeader
                  id="llm-api-base-url"
                  label="API Base URL"
                  required
                  help={
                    <ContextualHelpLink
                      href={PROFILE_HELP_LINKS.llmConfiguration}
                      compact
                    >
                      查看填写示例
                    </ContextualHelpLink>
                  }
                />
                <input
                  id="llm-api-base-url"
                  value={llmForm.api_base_url}
                  onChange={(event) =>
                    setLlmForm((previous) => ({
                      ...previous,
                      api_base_url: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：https://api.deepseek.com"
                />
                <p className="mt-2 text-xs leading-5 text-stone-500">
                  模型服务商提供的 OpenAI 兼容接口地址，不是平台官网地址。
                </p>
              </div>
              <div className="block">
                <FormFieldHeader
                  id="llm-api-key"
                  label="API Key"
                  required
                  help={
                    <ContextualHelpLink
                      href={PROFILE_HELP_LINKS.llmConfiguration}
                      compact
                    >
                      如何获取 API Key
                    </ContextualHelpLink>
                  }
                />
                <input
                  id="llm-api-key"
                  type="password"
                  value={llmForm.api_key}
                  onChange={(event) =>
                    setLlmForm((previous) => ({
                      ...previous,
                      api_key: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：sk-xxxxxxxxxxxxxxxx"
                />
                <p className="mt-2 text-xs leading-5 text-stone-500">
                  在模型服务商控制台创建，不是账号登录密码。
                </p>
              </div>
              <label className="block">
                {<FieldLabel label={"模型名称"} required={true} />}
                <input
                  value={llmForm.model_name}
                  onChange={(event) =>
                    setLlmForm((previous) => ({
                      ...previous,
                      model_name: event.target.value,
                    }))
                  }
                  className={inputClassName}
                  placeholder="示例：deepseek-v4-flash"
                />
              </label>
            </div>

            <div className="mt-6 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => void saveLLM()}
                disabled={submittingLLM}
                className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
              >
                {submittingLLM && <Loader2 className="h-4 w-4 animate-spin" />}
                保存模型
              </button>
              {editingLLM && (
                <>
                  <button
                    type="button"
                    onClick={() => void runLlmModelsFetch()}
                    disabled={fetchingLLMModels || !desktopBackendReady}
                    className={getActionButtonClassName(
                      llmModelsActionState,
                      fetchingLLMModels || !desktopBackendReady,
                    )}
                  >
                    {fetchingLLMModels ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : llmModelsActionState === "success" ? (
                      <CheckCircle2 className="h-4 w-4" />
                    ) : llmModelsActionState === "error" ? (
                      <XCircle className="h-4 w-4" />
                    ) : null}
                    获取模型列表
                  </button>
                  <button
                    type="button"
                    onClick={() => void runLlmConnectionTest()}
                    disabled={testingLLMConnection || !desktopBackendReady}
                    className={getActionButtonClassName(
                      llmProbeActionState,
                      testingLLMConnection || !desktopBackendReady,
                    )}
                  >
                    {testingLLMConnection ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : llmProbeActionState === "success" ? (
                      <CheckCircle2 className="h-4 w-4" />
                    ) : llmProbeActionState === "error" ? (
                      <XCircle className="h-4 w-4" />
                    ) : null}
                    测试模型
                  </button>
                  {selectedLlmProfileId === editingLLM.id ? (
                    <span className="inline-flex items-center rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-700">
                      当前使用中
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        void (async () => {
                          if (
                            !(await requestWorkspaceDraftGuard({
                              nextLlmProfileId: editingLLM.id,
                            }))
                          ) {
                            return;
                          }
                          setSelectedLlmProfileId(editingLLM.id);
                          notifySuccess(`当前模型：${editingLLM.name}`);
                        })();
                      }}
                      className="ui-btn-secondary"
                    >
                      设为当前
                    </button>
                  )}
                  {editingLLM.is_default ? (
                    <span className="inline-flex items-center rounded-xl border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-700">
                      已设为默认
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        void setDefaultLLMProfile(editingLLM.id)
                          .then(async () => {
                            await refreshSelections();
                            setLlmForm((previous) => ({
                              ...previous,
                              is_default: true,
                            }));
                            notifySuccess(`默认模型：${editingLLM.name}`);
                          })
                          .catch((defaultError) => {
                            notifyError(
                              "设为默认模型失败",
                              getActionErrorMessage(
                                defaultError,
                                "设置默认模型失败",
                              ),
                            );
                          });
                      }}
                      className="ui-btn-secondary"
                    >
                      设为默认
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => void openLLMDeletionImpact(editingLLM.id)}
                    className="ui-btn-danger inline-flex items-center gap-2"
                    disabled={loadingLLMDeletionImpactId === editingLLM.id}
                  >
                    {loadingLLMDeletionImpactId === editingLLM.id ? (
                      <Loader2
                        aria-hidden="true"
                        className="h-4 w-4 animate-spin"
                      />
                    ) : (
                      <Trash2 aria-hidden="true" className="h-4 w-4" />
                    )}
                    {loadingLLMDeletionImpactId === editingLLM.id
                      ? "正在检查"
                      : "删除"}
                  </button>
                </>
              )}
            </div>
            {(llmModelsResult || llmProbeResult) && (
              <div className="mt-5 rounded-[30px] border border-stone-200 bg-[linear-gradient(180deg,rgba(252,251,248,0.96),rgba(255,255,255,0.98))] p-4 shadow-sm shadow-stone-200/60">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold text-stone-900">
                      连接诊断
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2 text-[11px] text-stone-500">
                    {llmModelsResult ? (
                      <span className="rounded-full border border-stone-200 bg-white px-2.5 py-1">
                        1. 基础连通性
                      </span>
                    ) : null}
                    {llmProbeResult ? (
                      <span className="rounded-full border border-stone-200 bg-white px-2.5 py-1">
                        2. 测试模型
                      </span>
                    ) : null}
                  </div>
                </div>
                <div className="mt-4 space-y-4">
                  {llmModelsResult ? (
                    <div className="space-y-2">
                      <div className="pl-1 text-[11px] uppercase tracking-[0.22em] text-stone-400">
                        Step 1
                      </div>
                      <LlmModelsFeedbackPanel
                        result={llmModelsResult}
                        currentModelName={llmForm.model_name}
                        onSelectModel={handleSelectSuggestedModel}
                      />
                    </div>
                  ) : null}
                  {llmProbeResult ? (
                    <div className="space-y-2">
                      <div className="pl-1 text-[11px] uppercase tracking-[0.22em] text-stone-400">
                        Step 2
                      </div>
                      <LlmTestFeedbackPanel result={llmProbeResult} />
                    </div>
                  ) : null}
                </div>
              </div>
            )}
          </ProfileSetupSection>

          <ProfileSetupSection
            sectionId="test"
            title="测试写信"
            description="先给自己发送一封测试邮件。"
            open={openSetupSections.test}
            renderContent={renderedSetupSections.test}
            onToggle={() => toggleSetupSection("test")}
            onExitComplete={() => handleSetupSectionExitComplete("test")}
            sectionRef={setTestSetupSectionRef}
            badge={
              <span className="rounded-full border border-stone-200 bg-stone-50 px-3 py-1.5 text-xs text-stone-600">
                测试发信设置
              </span>
            }
          >
            <div className="mt-6">
              <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                <Send className="h-4 w-4 text-primary" />
                给自己发一封测试邮件
              </div>
              <p className="mt-2 text-sm leading-6 text-stone-600">
                检查模板、附件、模型和邮箱设置。
              </p>
              <p className="mt-2 text-sm leading-6 text-stone-500">
                仅用于测试，不会创建导师任务。
              </p>
              <div className="mt-4">
                <Link to="/test-compose" className="ui-btn-primary">
                  <Send className="h-4 w-4" />
                  开始测试
                </Link>
              </div>
            </div>

            <div className="mt-6 rounded-2xl border border-emerald-200 bg-emerald-50/80 px-4 py-4 text-sm leading-6 text-emerald-800">
              测试成功后，导入导师并创建任务。
            </div>
          </ProfileSetupSection>

          <CommunicationSharingPanel />

          <OtherSettingsCard />

          <AgentSupportCard />

          <DiagnosticLogPanel />

          <ProjectAcknowledgements />
        </div>
      )}
      <OutreachTemplateModal
        open={templateModalOpen}
        importingTemplateFile={importingTemplateFile}
        savingTemplate={savingOutreachTemplate}
        actingOnTemplate={actingOnOutreachTemplate}
        loadingTemplates={loadingOutreachTemplates}
        templates={activeOutreachTemplates}
        archivedTemplates={archivedOutreachTemplates}
        editorId={templateEditorId}
        form={outreachTemplateForm}
        identityLabel={editingIdentity ? "当前身份" : "新身份"}
        identityDefaultTemplateId={identityForm.default_outreach_template_id}
        onClose={() => setTemplateModalOpen(false)}
        onComplete={() =>
          void saveOutreachTemplate().then((saved) => {
            if (saved) {
              setTemplateModalOpen(false);
            }
          })
        }
        onCreate={beginOutreachTemplateCreation}
        onSelect={openOutreachTemplateEditor}
        onDuplicate={(templateId) =>
          void handleDuplicateOutreachTemplate(templateId)
        }
        onSetIdentityDefault={(template) =>
          void handleSetIdentityDefaultTemplate(template)
        }
        onClearIdentityDefault={() => void handleClearIdentityDefaultTemplate()}
        onSetGlobalDefault={(templateId) =>
          void handleSetGlobalDefaultTemplate(templateId)
        }
        onDelete={(template) => void handleDeleteOutreachTemplate(template)}
        onRestore={(template) => void handleRestoreOutreachTemplate(template)}
        onImport={(file) => void handleTemplateFileImport(file)}
        onNameChange={(value) =>
          setOutreachTemplateForm((previous) => ({
            ...previous,
            name: value,
          }))
        }
        onModeChange={(value) =>
          setOutreachTemplateForm((previous) => ({
            ...previous,
            outreach_generation_mode: value,
          }))
        }
        onSubjectChange={(value) =>
          setOutreachTemplateForm((previous) => ({
            ...previous,
            outreach_template_subject: value,
          }))
        }
        onBodyChange={({ html, text }) =>
          setOutreachTemplateForm((previous) => ({
            ...previous,
            outreach_template_body_text: text,
            outreach_template_body_html: html,
          }))
        }
      />
      {displayIdentity && (
        <MaterialLibraryModal
          open={materialModalOpen}
          identity={displayIdentity}
          materials={displayIdentity.materials}
          busy={actingOnMaterial || uploadingMaterial}
          uploading={uploadingMaterial}
          selectedMaterialType={newMaterialType}
          materialFilter={materialFilter}
          highlightedMaterialId={highlightedMaterialId}
          onChangeMaterialType={setNewMaterialType}
          onChangeMaterialFilter={setMaterialFilter}
          onUpload={(file) => void handleMaterialUpload(file)}
          onOpen={(material) => void handleOpenMaterial(material)}
          onDownload={(material) => void handleDownloadMaterial(material)}
          onClose={() => setMaterialModalOpen(false)}
          onSetPrimary={(material) => void handleSetPrimaryMaterial(material)}
          onDelete={(material) => void handleDeleteMaterial(material)}
        />
      )}
      {llmDeletionImpact && (
        <LLMDeletionDialog
          impact={llmDeletionImpact}
          replacementProfiles={llmProfiles.filter(
            (profile) => profile.id !== llmDeletionImpact.profile_id,
          )}
          replacementProfileId={replacementDefaultLLMId}
          busy={retiringLLM}
          onReplacementChange={setReplacementDefaultLLMId}
          onClose={() => {
            if (!retiringLLM) {
              setLlmDeletionImpact(null);
              setReplacementDefaultLLMId(null);
            }
          }}
          onConfirm={() => void retireSelectedLLM()}
        />
      )}
      {identityDeletionImpact && (
        <IdentityDeletionDialog
          impact={identityDeletionImpact}
          busy={deletingIdentity}
          onClose={() => {
            if (!deletingIdentity) {
              setIdentityDeletionImpact(null);
            }
          }}
          onConfirm={() => void deleteSelectedIdentity()}
        />
      )}
      {confirmDialog}
    </main>
  );
};
