import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BackgroundTaskNotificationProvider } from "@/app/providers/BackgroundTaskNotificationContext";
import { NotificationProvider } from "@/context/NotificationContext";
import { ProfessorsPage } from "@/pages/ProfessorsPage";

const mockedUseSelectionContext = vi.hoisted(() => vi.fn());
const listProfessorsForManagement = vi.hoisted(() => vi.fn());
const listProfessorTags = vi.hoisted(() => vi.fn());
const importProfessorsFromFile = vi.hoisted(() => vi.fn());

vi.mock("@/context/SelectionContext", () => ({
  useSelectionContext: mockedUseSelectionContext,
}));

vi.mock("@/entities/professor/api/professors", () => ({
  listProfessorTags,
  listProfessorsForManagement,
  archiveProfessor: vi.fn(),
  bulkArchiveProfessors: vi.fn(),
  createProfessor: vi.fn(),
  downloadProfessorExport: vi.fn(),
  downloadProfessorTemplate: vi.fn(),
  importProfessorsFromFile,
  restoreProfessor: vi.fn(),
  triggerCrawler: vi.fn(),
  updateProfessor: vi.fn(),
}));

vi.mock("@/entities/professor/api/informationEnrichment", () => ({
  createSingleProfessorInformationEnrichment: vi.fn(),
  getActiveProfessorInformationEnrichment: vi
    .fn()
    .mockResolvedValue({ active: false, job: null }),
  getProfessorInformationEnrichmentJob: vi.fn(),
  listProfessorInformationEnrichmentItems: vi.fn().mockResolvedValue([]),
  createProfessorInformationEnrichmentJob: vi.fn(),
}));

const renderPage = () =>
  render(
    <MemoryRouter>
      <NotificationProvider>
        <BackgroundTaskNotificationProvider>
          <ProfessorsPage />
        </BackgroundTaskNotificationProvider>
      </NotificationProvider>
    </MemoryRouter>,
  );

const getWorkbenchRegion = () =>
  screen.getByRole("region", { name: "导师管理" });

const openImportModal = () => {
  const button =
    within(getWorkbenchRegion()).queryByRole("button", { name: "选择文件" }) ??
    screen.getByRole("button", { name: "选择文件" });
  fireEvent.click(button);
};

describe("ProfessorsPage notifications", () => {
  beforeEach(() => {
    Reflect.deleteProperty(window, "autoEmailSender");
    mockedUseSelectionContext.mockReset();
    mockedUseSelectionContext.mockReturnValue({
      identities: [],
      llmProfiles: [],
      selectedIdentityId: 1,
      selectedLlmProfileId: 7,
      selectedIdentity: null,
      selectedLlmProfile: null,
      loading: false,
      setSelectedIdentityId: vi.fn(),
      setSelectedLlmProfileId: vi.fn(),
      refreshSelections: vi.fn(),
    });
    listProfessorsForManagement.mockReset();
    listProfessorsForManagement.mockResolvedValue([]);
    listProfessorTags.mockReset();
    listProfessorTags.mockResolvedValue([]);
    importProfessorsFromFile.mockReset();
  });

  it("shows the empty-import validation as a notification card", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalled();
    });

    openImportModal();
    fireEvent.click(screen.getByRole("button", { name: "开始导入" }));

    const notificationCard = await screen.findByTestId("notification-card");
    expect(notificationCard).toBeInTheDocument();
  });

  it("explains that import templates include guidance and ignored examples", async () => {
    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalled();
    });

    openImportModal();

    expect(screen.getByRole("button", { name: "开始导入" })).toBeInTheDocument();
    expect(document.querySelector('input[type="file"][accept=".csv,.xlsx"]')).not.toBeNull();
    expect(screen.getByRole("button", { name: "关闭" })).toBeInTheDocument();
  });

  it("keeps the import result detail card while showing a success notification after import", async () => {
    importProfessorsFromFile.mockResolvedValue({
      message: "已完成导师导入",
      inserted_count: 2,
      updated_count: 1,
      failed_count: 0,
    });

    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalledTimes(1);
    });

    openImportModal();

    const fileInput = document.querySelector(
      'input[type="file"][accept=".csv,.xlsx"]',
    ) as HTMLInputElement | null;

    expect(fileInput).not.toBeNull();

    fireEvent.change(fileInput as HTMLInputElement, {
      target: {
        files: [
          new File(["name,email\n张三,zhangsan@example.com"], "professors.xlsx", {
            type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          }),
        ],
      },
    });

    fireEvent.click(screen.getByRole("button", { name: "开始导入" }));

    await waitFor(() => {
      expect(importProfessorsFromFile).toHaveBeenCalledTimes(1);
    });

    const notificationCard = await screen.findByTestId("notification-card");
    expect(notificationCard).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始导入" })).toBeEnabled();
  });

  it("uses the desktop open-file bridge for professor import selection", async () => {
    const selectProfessorImportFile = vi.fn().mockResolvedValue({
      name: "professors.csv",
      type: "text/csv",
      data: new Uint8Array([110, 97, 109, 101, 44, 101, 109, 97, 105, 108]).buffer,
    });
    window.autoEmailSender = {
      getVersion: vi.fn(),
      selectProfessorImportFile,
      checkForUpdate: vi.fn(),
      downloadUpdate: vi.fn(),
      switchToFullDownload: vi.fn(),
      quitAndInstall: vi.fn(),
      onUpdateStatus: vi.fn(),
    } as NonNullable<typeof window.autoEmailSender>;

    renderPage();

    await waitFor(() => {
      expect(listProfessorsForManagement).toHaveBeenCalled();
    });

    openImportModal();
    fireEvent.click(screen.getByText("拖拽 csv/xlsx 到这里，或点击选择文件"));

    await waitFor(() => {
      expect(selectProfessorImportFile).toHaveBeenCalledTimes(1);
    });

    expect(screen.getByText("professors.csv")).toBeInTheDocument();
  });
});
