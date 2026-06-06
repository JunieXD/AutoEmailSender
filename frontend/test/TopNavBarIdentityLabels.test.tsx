import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { TopNavBar } from "@/components/organisms/TopNavBar";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: mockedUseSelectionContext,
}));
vi.mock("@/context/useWorkspaceDraftGuard", () => ({
  useWorkspaceDraftGuard: () => ({
    requestWorkspaceDraftGuard: vi.fn(async () => true),
  }),
}));

describe("TopNavBar identity labels", () => {
  it("uses the profile name in the identity selector", () => {
    mockedUseSelectionContext.mockReturnValue({
      identities: [
        {
          id: 1,
          name: "王同学",
          profile_name: "博士申请配置",
          sender_name: "王同学",
          is_default: true,
        },
      ],
      llmProfiles: [],
      selectedIdentityId: 1,
      selectedLlmProfileId: null,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId: vi.fn(),
      loading: false,
    });

    render(
      <MemoryRouter>
        <TopNavBar />
      </MemoryRouter>,
    );

    expect(screen.getByText("博士申请配置（默认）")).toBeInTheDocument();
    expect(screen.queryByText("王同学（默认）")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /身份/ }));

    expect(screen.getByRole("option", { name: "博士申请配置（默认）" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "王同学（默认）" })).not.toBeInTheDocument();
  });
});
