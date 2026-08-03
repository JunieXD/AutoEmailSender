import { MemoryRouter } from 'react-router-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  addVisibleRecordSelection,
  getVisibleRecordSelectionState,
} from '@/lib/communityMentorSelection';
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
  comparison_token: 'a'.repeat(64),
  category: 'new',
  local_professor_id: null,
  local_professor_name: null,
  local_archived: false,
  linked: false,
  identity_conflict: false,
  match_reason: null,
  import_blocked: false,
  import_blocked_reason: null,
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

const buildComparison = (index: number): CommunityMentorComparisonDTO => {
  const suffix = String(index + 1).padStart(4, '0');
  const email = `mentor${suffix}@example.edu`;
  return {
    ...comparison,
    comparison_token: index.toString(16).padStart(64, '0'),
    record: {
      ...comparison.record,
      id: `mentor_batch${suffix}`,
      name: `导师${suffix}`,
      email,
      contacts: comparison.record.contacts.map((contact) => ({
        ...contact,
        email,
      })),
    },
  };
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
    expect(apiMocks.getCatalog).toHaveBeenCalledWith(true);
    expect(notificationMocks.notifySuccess).not.toHaveBeenCalled();
  });

  it('keeps an offline-cache warning visible instead of relying on a toast', async () => {
    apiMocks.getCatalog.mockResolvedValue({
      ...populatedCatalog,
      source: 'cache',
      stale: true,
      warning: '网络刷新失败，正在使用最后一次验证成功的缓存',
    });

    renderPage();

    expect(
      await screen.findByText('当前显示的是上次验证成功的数据'),
    ).toBeInTheDocument();
    expect(screen.getByText(/网络刷新失败/)).toBeInTheDocument();
    expect(apiMocks.getCatalog).toHaveBeenCalledWith(true);
  });

  it('explains and blocks a unit that would exceed the 2000-record load limit', async () => {
    apiMocks.getCatalog.mockResolvedValue({
      ...populatedCatalog,
      record_count: 2001,
      universities: populatedCatalog.universities.map((university) => ({
        ...university,
        record_count: 2001,
        units: university.units.map((unit) => ({
          ...unit,
          record_count: 2001,
        })),
      })),
    });

    renderPage();

    const unitCheckbox = await screen.findByLabelText(/计算机学院/);
    fireEvent.click(unitCheckbox);

    expect(unitCheckbox).not.toBeChecked();
    expect(notificationMocks.notifyWarning).toHaveBeenCalledWith(
      '所选导师太多',
      expect.stringContaining('一次最多加载 2000 位'),
    );
    expect(apiMocks.listRecords).not.toHaveBeenCalled();
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
    expect(apiMocks.preview).toHaveBeenCalledWith({
      dataset_version: populatedCatalog.dataset_version,
      unit_paths: ['data/org_example_university/org_example_school.json'],
      record_ids: ['mentor_example0001'],
    });
    fireEvent.click(screen.getByRole('button', { name: '确认导入 1 位' }));

    await waitFor(() => expect(apiMocks.importRecords).toHaveBeenCalledTimes(1));
    expect(apiMocks.importRecords).toHaveBeenCalledWith({
      dataset_version: populatedCatalog.dataset_version,
      unit_paths: ['data/org_example_university/org_example_school.json'],
      items: [
        {
          community_record_id: comparison.record.id,
          comparison_token: comparison.comparison_token,
          field_choices: {
            name: 'community',
            email: 'community',
          },
          confirm_identity_match: true,
        },
      ],
    });
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

  it('finds mentors by alternate email and secondary affiliation', async () => {
    const searchableComparison: CommunityMentorComparisonDTO = {
      ...comparison,
      record: {
        ...comparison.record,
        contacts: [
          ...comparison.record.contacts,
          {
            email: 'alternate@example.edu',
            is_primary: false,
            affiliation_id: 'aff_example0002',
            source_url: 'https://example.edu/alternate',
            observed_at: '2026-08-03T00:00:00Z',
          },
        ],
        affiliations: [
          ...comparison.record.affiliations,
          {
            id: 'aff_example0002',
            organization_id: 'org_second_institute',
            status: 'current',
            is_primary: false,
            title: '访问教授',
            university: '第二大学',
            school: '数据研究院',
            department: '交叉研究中心',
            source_url: 'https://second.example.edu/profile',
            observed_at: '2026-08-03T00:00:00Z',
          },
        ],
      },
    };
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);
    apiMocks.listRecords.mockResolvedValue({
      ...recordsPayload,
      records: [searchableComparison],
    });

    renderPage();
    fireEvent.click(await screen.findByLabelText(/计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '加载所选学院' }));
    await screen.findByText('张老师');
    const searchInput = screen.getByPlaceholderText('姓名、全部邮箱、任职、方向');

    fireEvent.change(searchInput, { target: { value: '不存在的关键词' } });
    expect(screen.queryByText('张老师')).not.toBeInTheDocument();
    fireEvent.change(searchInput, { target: { value: 'alternate@example.edu' } });
    expect(screen.getByText('张老师')).toBeInTheDocument();
    fireEvent.change(searchInput, { target: { value: '访问教授' } });
    expect(screen.getByText('张老师')).toBeInTheDocument();
  });

  it('paginates large results and keeps 500 of 501 as a partial selection', async () => {
    const comparisons = Array.from({ length: 501 }, (_, index) => buildComparison(index));
    apiMocks.getCatalog.mockResolvedValue({
      ...populatedCatalog,
      record_count: comparisons.length,
      universities: populatedCatalog.universities.map((university) => ({
        ...university,
        record_count: comparisons.length,
        units: university.units.map((unit) => ({
          ...unit,
          record_count: comparisons.length,
        })),
      })),
    });
    apiMocks.listRecords.mockResolvedValue({
      ...recordsPayload,
      records: comparisons,
    });

    renderPage();
    fireEvent.click(await screen.findByLabelText(/计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '加载所选学院' }));
    expect(await screen.findByText('导师0001')).toBeInTheDocument();
    expect(screen.queryByText('导师0101')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findByText('导师0101')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', { name: '选择当前筛选结果' }));
    await waitFor(() => {
      const selectAll = screen.getByRole('checkbox', { name: '选择当前筛选结果' });
      expect(selectAll).not.toBeChecked();
      expect((selectAll as HTMLInputElement).indeterminate).toBe(true);
    });
    expect(screen.getByText(/已选 500\/501/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: '选择当前筛选结果' }));
    await waitFor(() => {
      const selectAll = screen.getByRole('checkbox', { name: '选择当前筛选结果' });
      expect(selectAll).not.toBeChecked();
      expect((selectAll as HTMLInputElement).indeterminate).toBe(true);
    });
    expect(notificationMocks.notifyWarning).toHaveBeenCalledWith(
      '已选择前 500 位导师',
      expect.stringContaining('还有 1 位未选中'),
    );
  });

  it('does not offer a community empty value that would clear local data', async () => {
    const localOnlyComparison: CommunityMentorComparisonDTO = {
      ...comparison,
      category: 'conflict',
      local_professor_id: 42,
      local_professor_name: '张老师',
      fields: [
        {
          field: 'department',
          label: '系所',
          local_value: '本地系所',
          community_value: null,
          baseline_present: false,
          baseline_value: null,
          state: 'local_only',
          suggested_choice: 'local',
        },
      ],
    };
    const localOnlyPayload = {
      ...recordsPayload,
      records: [localOnlyComparison],
    };
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);
    apiMocks.listRecords.mockResolvedValue(localOnlyPayload);
    apiMocks.preview.mockResolvedValue(localOnlyPayload);

    renderPage();
    fireEvent.click(await screen.findByLabelText(/计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '加载所选学院' }));
    await screen.findByText('zhang@example.edu');
    fireEvent.click(screen.getByLabelText('选择 张老师'));
    fireEvent.click(screen.getByRole('button', { name: /预览并导入 1/ }));

    expect(await screen.findByRole('button', { name: '采用社区系所' })).toBeDisabled();
    expect(screen.getByText(/不能用空值清掉本地资料/)).toBeInTheDocument();
  });

  it('selects at most 500 mentors from the current filter and reports the remainder', () => {
    const visibleRecordIds = Array.from(
      { length: 501 },
      (_, index) => `mentor_example${String(index).padStart(4, '0')}`,
    );

    const result = addVisibleRecordSelection([], visibleRecordIds);

    expect(result.recordIds).toHaveLength(500);
    expect(result.recordIds).toEqual(visibleRecordIds.slice(0, 500));
    expect(result.omittedCount).toBe(1);
    expect(
      getVisibleRecordSelectionState(result.recordIds, visibleRecordIds),
    ).toEqual({
      selectedVisibleCount: 500,
      allVisibleSelected: false,
      partiallyVisibleSelected: true,
    });
  });

  it('keeps the loaded list but disables preview when the selected units change', async () => {
    const secondPath = 'data/org_example_university/org_example_institute.json';
    apiMocks.getCatalog.mockResolvedValue({
      ...populatedCatalog,
      record_count: 2,
      universities: populatedCatalog.universities.map((university) => ({
        ...university,
        record_count: 2,
        units: [
          ...university.units,
          {
            id: 'org_example_institute',
            name: '人工智能研究院',
            type: 'institute' as const,
            record_count: 1,
            path: secondPath,
          },
        ],
      })),
    });
    apiMocks.listRecords.mockResolvedValue(recordsPayload);

    renderPage();
    fireEvent.click(await screen.findByLabelText(/计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '加载所选学院' }));
    await screen.findByText('zhang@example.edu');
    fireEvent.click(screen.getByLabelText('选择 张老师'));
    fireEvent.click(screen.getByLabelText(/人工智能研究院/));

    expect(
      await screen.findByText(/当前列表来自上一次加载/),
    ).toBeInTheDocument();
    expect(screen.getByText(/已选择 1\/500/)).toBeInTheDocument();
    const previewButton = screen.getByRole('button', { name: /预览并导入 1/ });
    expect(previewButton).toBeDisabled();
    fireEvent.click(previewButton);
    expect(apiMocks.preview).not.toHaveBeenCalled();
  });

  it('blocks a mentor that becomes unsafe during preview and shows the next action', async () => {
    const blockedComparison: CommunityMentorComparisonDTO = {
      ...comparison,
      category: 'conflict',
      identity_conflict: true,
      match_reason: '该本地导师已关联另一条社区记录',
      import_blocked: true,
      import_blocked_reason: '请先处理原有关联，再重新加载社区导师列表',
    };
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);
    apiMocks.listRecords.mockResolvedValue(recordsPayload);
    apiMocks.preview.mockResolvedValue({
      ...recordsPayload,
      records: [blockedComparison],
    });

    renderPage();
    fireEvent.click(await screen.findByLabelText(/计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '加载所选学院' }));
    await screen.findByText('zhang@example.edu');
    fireEvent.click(screen.getByLabelText('选择 张老师'));
    fireEvent.click(screen.getByRole('button', { name: /预览并导入 1/ }));

    expect(await screen.findByText(/暂不可导入：/)).toBeInTheDocument();
    expect(screen.getByText(/请先处理原有关联/)).toBeInTheDocument();
    expect(screen.getByLabelText('选择 张老师')).toBeDisabled();
    expect(screen.queryByText(/人工确认同一导师/)).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: '社区导师导入预览' })).not.toBeInTheDocument();
    expect(apiMocks.importRecords).not.toHaveBeenCalled();
  });
});
