import { MemoryRouter } from 'react-router-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CommunityMentorsPage } from '@/pages/CommunityMentorsPage';
import type {
  CommunityCatalogDTO,
  CommunityMentorComparisonDTO,
  CommunityRecordsDTO,
} from '@/types';


const apiMocks = vi.hoisted(() => ({
  getCatalog: vi.fn(),
  listRecords: vi.fn(),
  preview: vi.fn(),
  importRecords: vi.fn(),
}));

const notificationMocks = vi.hoisted(() => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
  notifyWarning: vi.fn(),
}));

const openExternalHttpUrl = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api/communityMentorsApi', () => ({
  getCommunityMentorCatalog: (...args: unknown[]) => apiMocks.getCatalog(...args),
  listCommunityMentors: (...args: unknown[]) => apiMocks.listRecords(...args),
  previewCommunityMentorImport: (...args: unknown[]) => apiMocks.preview(...args),
  importCommunityMentors: (...args: unknown[]) => apiMocks.importRecords(...args),
}));

vi.mock('@/context/NotificationContext', () => ({
  useNotification: () => notificationMocks,
}));

vi.mock('@/lib/externalUrls', () => ({
  openExternalHttpUrl,
}));

const emptyCatalog: CommunityCatalogDTO = {
  schema_version: 1,
  dataset_version: '2026-08-03T000000Z-abcdef123456',
  generated_at: '2026-08-03T00:00:00Z',
  record_count: 0,
  universities: [],
  source: 'network',
  stale: false,
  warning: null,
  verified_at: '2026-08-03T00:00:00Z',
  lifecycle_warnings: [],
};

const populatedCatalog: CommunityCatalogDTO = {
  ...emptyCatalog,
  record_count: 1,
  universities: [
    {
      id: 'org_example_university',
      name: '示例大学',
      record_count: 1,
      units: [
        {
          id: 'org_example_school',
          name: '计算机学院',
          type: 'school',
          record_count: 1,
          path: 'data/org_example_university/org_example_school.json',
        },
      ],
    },
  ],
};

const comparison: CommunityMentorComparisonDTO = {
  record: {
    id: 'mentor_example0001',
    name: '张老师',
    email: 'zhang@example.edu',
    title: '教授',
    university: '示例大学',
    school: '计算机学院',
    department: '人工智能系',
    research_direction: '智能体',
    recent_papers: ['Example Paper'],
    profile_url: 'https://example.edu/profile',
    source_url: 'https://example.edu/source',
    status: 'active',
    last_verified_at: '2026-08-03T00:00:00Z',
    contacts: [
      {
        email: 'zhang@example.edu',
        is_primary: true,
        affiliation_id: 'aff_example0001',
        source_url: 'https://example.edu/source',
        observed_at: '2026-08-03T00:00:00Z',
      },
    ],
    affiliations: [
      {
        id: 'aff_example0001',
        organization_id: 'org_example_school',
        status: 'current',
        is_primary: true,
        title: '教授',
        university: '示例大学',
        school: '计算机学院',
        department: '人工智能系',
        source_url: 'https://example.edu/source',
        observed_at: '2026-08-03T00:00:00Z',
      },
    ],
    contributors: [
      {
        github_user_id: 12345,
        github_login_at_submission: 'example-user',
        issue_urls: ['https://github.com/example/repo/issues/1'],
      },
    ],
  },
  category: 'new',
  local_professor_id: null,
  local_professor_name: null,
  local_archived: false,
  linked: false,
  identity_conflict: false,
  match_reason: null,
  fields: [
    {
      field: 'name',
      label: '姓名',
      local_value: null,
      community_value: '张老师',
      baseline_present: false,
      baseline_value: null,
      state: 'new',
      suggested_choice: 'community',
    },
    {
      field: 'email',
      label: '主邮箱',
      local_value: null,
      community_value: 'zhang@example.edu',
      baseline_present: false,
      baseline_value: null,
      state: 'new',
      suggested_choice: 'community',
    },
  ],
};

const recordsPayload: CommunityRecordsDTO = {
  dataset_version: populatedCatalog.dataset_version,
  source: 'cache',
  stale: false,
  warning: null,
  records: [comparison],
  lifecycle_warnings: [],
};

const renderPage = () =>
  render(
    <MemoryRouter>
      <CommunityMentorsPage />
    </MemoryRouter>,
  );

describe('CommunityMentorsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it('renders a useful empty-library state', async () => {
    apiMocks.getCatalog.mockResolvedValue(emptyCatalog);

    renderPage();

    expect(await screen.findByText('社区库正在起步')).toBeInTheDocument();
    expect(screen.getByText('成为第一批贡献者')).toBeInTheDocument();
  });

  it('loads a selected unit, previews fields, and imports without opening a browser', async () => {
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);
    apiMocks.listRecords.mockResolvedValue(recordsPayload);
    apiMocks.preview.mockResolvedValue(recordsPayload);
    apiMocks.importRecords.mockResolvedValue({
      inserted_count: 1,
      updated_count: 0,
      linked_count: 0,
      skipped_count: 0,
      message: '社区导入完成',
      professors: [
        {
          community_record_id: 'mentor_example0001',
          professor_id: 1,
          action: 'inserted',
        },
      ],
    });

    renderPage();

    fireEvent.click(await screen.findByLabelText(/计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '加载所选学院' }));
    expect(await screen.findByText('zhang@example.edu')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('选择 张老师'));
    fireEvent.click(screen.getByRole('button', { name: /预览并导入 1/ }));
    expect(await screen.findByRole('dialog', { name: '社区导师导入预览' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认导入 1 位' }));

    await waitFor(() => expect(apiMocks.importRecords).toHaveBeenCalledTimes(1));
    expect(openExternalHttpUrl).not.toHaveBeenCalled();
    expect(notificationMocks.notifySuccess).toHaveBeenCalledWith(
      '社区导师已导入',
      '社区导入完成',
    );
  });

  it('opens GitHub only for explicit error feedback and copies the stable id', async () => {
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);
    apiMocks.listRecords.mockResolvedValue(recordsPayload);

    renderPage();
    fireEvent.click(await screen.findByLabelText(/计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '加载所选学院' }));
    await screen.findByText('zhang@example.edu');
    fireEvent.click(screen.getByRole('button', { name: /反馈错误/ }));

    expect(openExternalHttpUrl).toHaveBeenCalledWith(
      expect.stringContaining('template=report-error.yml'),
    );
    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        expect.stringContaining('社区导师 ID：mentor_example0001'),
      ),
    );
  });
});
