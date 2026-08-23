import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NotificationProvider } from "@/context/NotificationContext";
import {
  SelectionProvider,
  useSelectionContext,
} from "@/context/SelectionContext";
import type { IdentityDTO, LLMProfileDTO } from "@/types";

const listIdentities = vi.hoisted(() => vi.fn());
const listCommunicationGroups = vi.hoisted(() => vi.fn());
const listLLMProfiles = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api/communicationGroups", () => ({
  listCommunicationGroups,
}));

vi.mock("@/lib/api/identities", () => ({
  listIdentities,
}));

vi.mock("@/lib/api/llmProfiles", () => ({
  listLLMProfiles,
}));

const makeIdentity = (overrides: Partial<IdentityDTO>): IdentityDTO => ({
  id: 1,
  name: "默认身份",
  profile_name: "默认身份",
  sender_name: "默认身份",
  email_address: "default@example.com",
  smtp_host: "smtp.example.com",
  smtp_port: 465,
  smtp_username: "default@example.com",
  smtp_password: "secret",
  imap_host: null,
  imap_port: null,
  imap_username: null,
  imap_password: null,
  default_language: "zh-CN",
  outreach_generation_mode: "llm",
  outreach_template_subject: "默认主题",
  outreach_template_body_text: "默认正文",
  outreach_template_body_html: "<p>默认正文</p>",
  current_primary_material_id: null,
  current_primary_material: null,
  communication_group_id: null,
  match_threshold: null,
  daily_send_limit: null,
  send_interval_min: null,
  send_interval_max: null,
  same_domain_cooldown_minutes: null,
  is_default: true,
  materials: [],
  created_at: "2026-04-22T00:00:00Z",
  updated_at: "2026-04-22T00:00:00Z",
  ...overrides,
});

const makeLlmProfile = (overrides: Partial<LLMProfileDTO>): LLMProfileDTO => ({
  id: 1,
  name: "默认模型",
  provider: "openai",
  api_base_url: null,
  api_key: "test-key",
  model_name: "gpt-default",
  matcher_prompt_template: null,
  writer_prompt_template: null,
  temperature: null,
  max_tokens: null,
  is_default: true,
  created_at: "2026-04-22T00:00:00Z",
  updated_at: "2026-04-22T00:00:00Z",
  ...overrides,
});

const SelectionHarness = () => {
  const { selectedIdentity, selectedLlmProfile } = useSelectionContext();

  return (
    <div>
      <span>{selectedIdentity?.name ?? "no identity"}</span>
      <span>{selectedLlmProfile?.name ?? "no llm"}</span>
    </div>
  );
};

const SelectionSwitchHarness = () => {
  const { selectedLlmProfile, setSelectedLlmProfileId } = useSelectionContext();

  return (
    <div>
      <span data-testid="selected-llm">
        {selectedLlmProfile?.name ?? "no llm"}
      </span>
      <button type="button" onClick={() => setSelectedLlmProfileId(2)}>
        切换到备用模型
      </button>
    </div>
  );
};

const CommunicationScopeHarness = () => {
  const {
    communicationGroups,
    communicationIdentityIds,
    communicationScopeKey,
  } = useSelectionContext();

  return (
    <div>
      <span data-testid="communication-group-count">
        {communicationGroups.length}
      </span>
      <span data-testid="communication-identity-ids">
        {communicationIdentityIds.join(",")}
      </span>
      <span data-testid="communication-scope-key">{communicationScopeKey}</span>
    </div>
  );
};

const MatchScopeHarness = () => {
  const {
    matchSourceIdentity,
    matchUsesGroupSource,
    matchScopeKey,
  } = useSelectionContext();

  return (
    <div>
      <span data-testid="match-source-name">
        {matchSourceIdentity?.profile_name ?? "no source"}
      </span>
      <span data-testid="match-uses-group-source">
        {String(matchUsesGroupSource)}
      </span>
      <span data-testid="match-scope-key">{matchScopeKey}</span>
    </div>
  );
};

describe("SelectionContext notifications", () => {
  beforeEach(() => {
    listIdentities.mockReset();
    listCommunicationGroups.mockReset();
    listLLMProfiles.mockReset();
    listCommunicationGroups.mockResolvedValue([]);
    window.localStorage.clear();
  });

  it("shows a global notification card when the initial refresh fails", async () => {
    listIdentities.mockRejectedValue(new Error("加载全局上下文失败"));
    listLLMProfiles.mockResolvedValue([]);

    render(
      <NotificationProvider>
        <SelectionProvider>
          <SelectionHarness />
        </SelectionProvider>
      </NotificationProvider>,
    );

    await waitFor(() => {
      const cards = screen.getAllByTestId("notification-card");
      expect(
        cards.some((card) => card.textContent?.includes("加载全局上下文失败")),
      ).toBe(true);
    });
  });

  it("selects the first available identity and llm profile after bootstrap", async () => {
    listIdentities.mockResolvedValue([
      {
        id: 1,
        name: "测试身份",
        profile_name: "测试身份",
        sender_name: "测试身份",
        email_address: "sender@example.com",
        smtp_host: "smtp.example.com",
        smtp_port: 465,
        smtp_username: "sender@example.com",
        smtp_password: "secret",
        imap_host: null,
        imap_port: null,
        imap_username: null,
        imap_password: null,
        default_language: "zh-CN",
        outreach_generation_mode: "llm",
        outreach_template_subject: "测试主题",
        outreach_template_body_text: "测试正文",
        outreach_template_body_html: "<p>测试正文</p>",
        current_primary_material_id: null,
        current_primary_material: null,
        communication_group_id: null,
        match_threshold: null,
        daily_send_limit: null,
        send_interval_min: null,
        send_interval_max: null,
        same_domain_cooldown_minutes: null,
        is_default: true,
        materials: [],
        created_at: "2026-04-22T00:00:00Z",
        updated_at: "2026-04-22T00:00:00Z",
      },
    ]);
    listLLMProfiles.mockResolvedValue([
      {
        id: 1,
        name: "测试模型",
        provider: "openai",
        api_base_url: null,
        api_key: "test-key",
        model_name: "gpt-test",
        matcher_prompt_template: null,
        writer_prompt_template: null,
        temperature: null,
        max_tokens: null,
        is_default: true,
        created_at: "2026-04-22T00:00:00Z",
        updated_at: "2026-04-22T00:00:00Z",
      },
    ]);

    render(
      <NotificationProvider>
        <SelectionProvider>
          <SelectionHarness />
        </SelectionProvider>
      </NotificationProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("测试身份")).toBeInTheDocument();
      expect(screen.getByText("测试模型")).toBeInTheDocument();
    });
  });

  it("restores stored identity and llm profile after bootstrap", async () => {
    window.localStorage.setItem("selected_identity_id", "2");
    window.localStorage.setItem("selected_llm_profile_id", "2");
    listIdentities.mockResolvedValue([
      makeIdentity({ id: 1, is_default: true }),
      makeIdentity({
        id: 2,
        name: "手动身份",
        profile_name: "手动身份",
        sender_name: "手动身份",
        email_address: "selected@example.com",
        smtp_username: "selected@example.com",
        outreach_template_subject: "手动主题",
        outreach_template_body_text: "手动正文",
        outreach_template_body_html: "<p>手动正文</p>",
        is_default: false,
      }),
    ]);
    listLLMProfiles.mockResolvedValue([
      makeLlmProfile({ id: 1, is_default: true }),
      makeLlmProfile({
        id: 2,
        name: "手动模型",
        api_key: "test-key-2",
        model_name: "gpt-selected",
        is_default: false,
      }),
    ]);

    render(
      <NotificationProvider>
        <SelectionProvider>
          <SelectionHarness />
        </SelectionProvider>
      </NotificationProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("手动身份")).toBeInTheDocument();
      expect(screen.getByText("手动模型")).toBeInTheDocument();
      expect(window.localStorage.getItem("selected_identity_id")).toBe("2");
      expect(window.localStorage.getItem("selected_llm_profile_id")).toBe("2");
    });
  });

  it("keeps a manually selected llm profile instead of bouncing back to the stored id", async () => {
    window.localStorage.setItem("selected_llm_profile_id", "1");
    listIdentities.mockResolvedValue([]);
    listLLMProfiles.mockResolvedValue([
      {
        id: 1,
        name: "主模型",
        provider: "openai",
        api_base_url: null,
        api_key: "test-key-1",
        model_name: "gpt-main",
        matcher_prompt_template: null,
        writer_prompt_template: null,
        temperature: null,
        max_tokens: null,
        is_default: true,
        created_at: "2026-04-22T00:00:00Z",
        updated_at: "2026-04-22T00:00:00Z",
      },
      {
        id: 2,
        name: "备用模型",
        provider: "openai",
        api_base_url: null,
        api_key: "test-key-2",
        model_name: "gpt-backup",
        matcher_prompt_template: null,
        writer_prompt_template: null,
        temperature: null,
        max_tokens: null,
        is_default: false,
        created_at: "2026-04-22T00:00:00Z",
        updated_at: "2026-04-22T00:00:00Z",
      },
    ]);

    render(
      <NotificationProvider>
        <SelectionProvider>
          <SelectionSwitchHarness />
        </SelectionProvider>
      </NotificationProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("selected-llm")).toHaveTextContent("主模型");
    });

    fireEvent.click(screen.getByRole("button", { name: "切换到备用模型" }));

    await waitFor(() => {
      expect(screen.getByTestId("selected-llm")).toHaveTextContent("备用模型");
      expect(window.localStorage.getItem("selected_llm_profile_id")).toBe("2");
    });
  });

  it("does not silently replace a stored llm profile that no longer exists", async () => {
    window.localStorage.setItem("selected_llm_profile_id", "99");
    listIdentities.mockResolvedValue([]);
    listLLMProfiles.mockResolvedValue([
      makeLlmProfile({ id: 1, name: "仍可用的默认模型", is_default: true }),
    ]);

    render(
      <NotificationProvider>
        <SelectionProvider>
          <SelectionHarness />
        </SelectionProvider>
      </NotificationProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("no llm")).toBeInTheDocument();
      expect(window.localStorage.getItem("selected_llm_profile_id")).toBeNull();
    });
  });

  it("clears a retired selection across windows without choosing a fallback", async () => {
    window.localStorage.setItem("selected_llm_profile_id", "1");
    listIdentities.mockResolvedValue([]);
    listLLMProfiles
      .mockResolvedValueOnce([
        makeLlmProfile({ id: 1, name: "即将退役", is_default: true }),
        makeLlmProfile({ id: 2, name: "备用模型", is_default: false }),
      ])
      .mockResolvedValue([
        makeLlmProfile({ id: 2, name: "备用模型", is_default: true }),
      ]);

    render(
      <NotificationProvider>
        <SelectionProvider>
          <SelectionHarness />
        </SelectionProvider>
      </NotificationProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText("即将退役")).toBeInTheDocument();
    });

    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "llm_profile_retired_event",
        newValue: JSON.stringify({ profileId: 1, retiredAt: Date.now() }),
      }),
    );

    await waitFor(() => {
      expect(screen.getByText("no llm")).toBeInTheDocument();
      expect(screen.queryByText("备用模型")).not.toBeInTheDocument();
      expect(window.localStorage.getItem("selected_llm_profile_id")).toBeNull();
    });
  });

  it("derives a stable communication scope from the selected identity group", async () => {
    window.localStorage.setItem("selected_identity_id", "2");
    listIdentities.mockResolvedValue([
      makeIdentity({ id: 1, communication_group_id: 7 }),
      makeIdentity({
        id: 2,
        name: "共享身份",
        profile_name: "共享身份",
        email_address: "shared@example.com",
        smtp_username: "shared@example.com",
        communication_group_id: 7,
      }),
      makeIdentity({
        id: 3,
        name: "独立身份",
        profile_name: "独立身份",
        email_address: "solo@example.com",
        smtp_username: "solo@example.com",
        communication_group_id: null,
      }),
    ]);
    listCommunicationGroups.mockResolvedValue([
      {
        id: 7,
        members: [
          {
            id: 1,
            profile_name: "默认身份",
            email_address: "default@example.com",
            is_default: true,
          },
          {
            id: 2,
            profile_name: "共享身份",
            email_address: "shared@example.com",
            is_default: false,
          },
        ],
        created_at: "2026-07-21T00:00:00Z",
        updated_at: "2026-07-21T00:00:00Z",
      },
    ]);
    listLLMProfiles.mockResolvedValue([]);

    render(
      <NotificationProvider>
        <SelectionProvider>
          <CommunicationScopeHarness />
        </SelectionProvider>
      </NotificationProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("communication-group-count")).toHaveTextContent("1");
      expect(screen.getByTestId("communication-identity-ids")).toHaveTextContent("1,2");
      expect(screen.getByTestId("communication-scope-key")).toHaveTextContent("1:2");
    });
  });

  it("resolves the configured group member as the shared match source", async () => {
    window.localStorage.setItem("selected_identity_id", "2");
    listIdentities.mockResolvedValue([
      makeIdentity({ id: 1, communication_group_id: 7 }),
      makeIdentity({
        id: 2,
        name: "共享身份",
        profile_name: "共享身份",
        email_address: "shared@example.com",
        smtp_username: "shared@example.com",
        communication_group_id: 7,
        is_default: false,
      }),
    ]);
    listCommunicationGroups.mockResolvedValue([
      {
        id: 7,
        match_source_identity_id: 1,
        members: [
          {
            id: 1,
            profile_name: "默认身份",
            email_address: "default@example.com",
            is_default: true,
          },
          {
            id: 2,
            profile_name: "共享身份",
            email_address: "shared@example.com",
            is_default: false,
          },
        ],
        created_at: "2026-07-21T00:00:00Z",
        updated_at: "2026-07-21T00:00:00Z",
      },
    ]);
    listLLMProfiles.mockResolvedValue([]);

    render(
      <NotificationProvider>
        <SelectionProvider>
          <MatchScopeHarness />
        </SelectionProvider>
      </NotificationProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("match-source-name")).toHaveTextContent(
        "默认身份",
      );
      expect(screen.getByTestId("match-uses-group-source")).toHaveTextContent(
        "true",
      );
      expect(screen.getByTestId("match-scope-key")).toHaveTextContent("2:1");
    });
  });
});
