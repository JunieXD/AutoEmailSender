import { MemoryRouter } from 'react-router-dom';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  addFilteredCommunityUnitSelection,
  addVisibleRecordSelection,
  getVisibleRecordSelectionState,
} from '@/lib/communityMentorSelection';
import { resetCommunityMentorCatalogSessionCacheForTests } from '@/entities/community-mentor/api/catalogCache';
import { resetCommunityMentorPageSessionSnapshotForTests } from '@/lib/communityMentorPageState';
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
const scrollIntoView = vi.hoisted(() => vi.fn());

vi.mock('@/entities/community-mentor/api/communityMentors', () => ({
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
  schema_version: 2,
  dataset_version: 'v2-0123456789abcdef0123456789abcdef',
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
          path: 'objects/sha256/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json',
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
    resetCommunityMentorCatalogSessionCacheForTests();
    resetCommunityMentorPageSessionSnapshotForTests();
    document.body.style.overflow = '';
    document.documentElement.style.overflow = '';
    scrollIntoView.mockReset();
    Object.defineProperty(window.HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it('renders a useful empty-library state', async () => {
    apiMocks.getCatalog.mockResolvedValue(emptyCatalog);

    renderPage();

    expect(await screen.findByText('还没有导师数据')).toBeInTheDocument();
    expect(screen.getByText('按学校和学院查找导师，预览后导入本地。')).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: '批量贡献第一所学校/学院' }),
    ).toHaveAttribute('href', '/professors?community_contribution=batch');
    expect(
      screen.getByRole('link', { name: '贡献院校数据' }),
    ).toHaveAttribute('href', '/professors?community_contribution=batch');
    expect(screen.queryByRole('button', { name: '贡献一位导师' })).not.toBeInTheDocument();
    expect(apiMocks.getCatalog).toHaveBeenCalledWith(false);
    expect(notificationMocks.notifySuccess).not.toHaveBeenCalled();
    expect(screen.queryByText('版本化、可追踪来源、导入前逐项确认')).not.toBeInTheDocument();
    expect(screen.queryByText('数据版本')).not.toBeInTheDocument();
    expect(screen.queryByText('最后验证缓存')).not.toBeInTheDocument();
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
      await screen.findByText('正在使用缓存数据'),
    ).toBeInTheDocument();
    expect(screen.getByText(/网络刷新失败/)).toBeInTheDocument();
    expect(apiMocks.getCatalog).toHaveBeenNthCalledWith(1, false);
    await waitFor(() => expect(apiMocks.getCatalog).toHaveBeenCalledWith(true));
  });

  it('shows a relocation warning without presenting it as retirement', async () => {
    apiMocks.getCatalog.mockResolvedValue({
      ...populatedCatalog,
      lifecycle_warnings: [
        {
          community_record_id: 'mentor_example0001',
          professor_id: 1,
          professor_name: '张老师',
          status: 'relocated',
          reason: '导师主要任职已从示例大学调动至样本大学',
          source_url: 'https://example.edu/source',
          observed_at: '2026-08-03T00:00:00Z',
        },
      ],
    });

    renderPage();

    expect(await screen.findByText('已导入导师有生命周期变化')).toBeInTheDocument();
    expect(screen.getByText('· 已调动任职')).toBeInTheDocument();
    expect(screen.queryByText('· 已退休')).not.toBeInTheDocument();
  });

  it('reuses the session catalog immediately and refreshes when returning to the page', async () => {
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);

    const firstRender = renderPage();
    expect(await screen.findByText('示例大学')).toBeInTheDocument();
    expect(apiMocks.getCatalog).toHaveBeenCalledTimes(1);
    firstRender.unmount();

    renderPage();

    expect(screen.getByText('示例大学')).toBeInTheDocument();
    expect(screen.queryByText('正在加载社区导师库…')).not.toBeInTheDocument();
    await waitFor(() => expect(apiMocks.getCatalog).toHaveBeenCalledTimes(2));
    expect(apiMocks.getCatalog).toHaveBeenNthCalledWith(2, true);
  });

  it('restores the loaded list, filters, selection, and open preview after returning', async () => {
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);
    apiMocks.listRecords.mockResolvedValue(recordsPayload);
    apiMocks.preview.mockResolvedValue(recordsPayload);

    const firstRender = renderPage();
    fireEvent.click(await screen.findByLabelText(/选择 示例大学 计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '查看导师' }));
    await screen.findByText('zhang@example.edu');
    fireEvent.change(screen.getByLabelText('搜索导师'), {
      target: { value: '张老师' },
    });
    fireEvent.click(screen.getByLabelText('选择 张老师'));
    fireEvent.click(screen.getByRole('button', { name: /预览并导入 1/ }));
    expect(
      await screen.findByRole('dialog', { name: '社区导师导入预览' }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('搜索导入预览导师'), {
      target: { value: '张老师' },
    });
    await waitFor(() => expect(document.body.style.overflow).toBe('hidden'));

    firstRender.unmount();
    expect(document.body.style.overflow).toBe('');
    renderPage();

    expect(
      screen.getByRole('dialog', { name: '社区导师导入预览' }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('搜索导入预览导师')).toHaveValue('张老师');
    expect(screen.getByLabelText('搜索导师')).toHaveValue('张老师');
    expect(screen.getByText(/已加载 1 位 · 已选 1 位/)).toBeInTheDocument();
    expect(apiMocks.listRecords).toHaveBeenCalledTimes(1);
    expect(apiMocks.preview).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(document.body.style.overflow).toBe('hidden'));

    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    await waitFor(() => expect(document.body.style.overflow).toBe(''));
  });

  it('uses a stable vertical layout and points to the selector above', async () => {
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);

    renderPage();

    const layout = await screen.findByTestId('community-mentor-browser-layout');
    const selector = screen.getByTestId('community-mentor-unit-selector');
    expect(layout).toHaveClass('space-y-6');
    expect(layout).not.toHaveClass('lg:grid-cols-[21rem,minmax(0,1fr)]');
    expect(selector.className).not.toMatch(/sticky|top-/);
    expect(screen.getByText('选择学院后查看导师')).toBeInTheDocument();
    expect(screen.queryByText('先从左侧选择学院')).not.toBeInTheDocument();
  });

  it('treats the university and unit as peer labels and toggles from the card body', async () => {
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);

    renderPage();

    const unitCard = await screen.findByRole('button', {
      name: '选择 示例大学 计算机学院',
    });
    expect(within(unitCard).getByText('示例大学')).toHaveClass('text-sm', 'font-normal');
    expect(within(unitCard).getByText('计算机学院')).toHaveClass('text-sm', 'font-normal');
    expect(within(unitCard).getByText('计算机学院')).not.toHaveClass('font-semibold');

    fireEvent.click(within(unitCard).getByText('示例大学'));
    expect(unitCard).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(within(unitCard).getByText('计算机学院'));
    expect(unitCard).toHaveAttribute('aria-pressed', 'false');
  });

  it('keeps cached data usable while checking for updates in the background', async () => {
    let resolveRefresh: ((value: CommunityCatalogDTO) => void) | undefined;
    apiMocks.getCatalog
      .mockResolvedValueOnce({ ...populatedCatalog, source: 'cache' })
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveRefresh = resolve;
      }));

    renderPage();

    expect(await screen.findByText('示例大学')).toBeInTheDocument();
    await waitFor(() => expect(apiMocks.getCatalog).toHaveBeenCalledTimes(2));
    expect(apiMocks.getCatalog).toHaveBeenNthCalledWith(1, false);
    expect(apiMocks.getCatalog).toHaveBeenNthCalledWith(2, true);
    expect(screen.queryByText('正在加载社区导师库…')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新社区目录' })).toBeDisabled();

    resolveRefresh?.(populatedCatalog);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '刷新社区目录' })).toBeEnabled();
    });
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

    const unitToggle = await screen.findByLabelText(/计算机学院/);
    fireEvent.click(unitToggle);

    expect(unitToggle).toHaveAttribute('aria-pressed', 'false');
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
    fireEvent.click(screen.getByRole('button', { name: '查看导师' }));
    expect(await screen.findByText('zhang@example.edu')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('选择 张老师'));
    fireEvent.click(screen.getByRole('button', { name: /预览并导入 1/ }));
    expect(await screen.findByRole('dialog', { name: '社区导师导入预览' })).toBeInTheDocument();
    expect(screen.getByText('将按社区资料新增到本地。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '采用社区姓名' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', {
      name: '当前筛选的姓名全部保留本地',
    })).toBeDisabled();
    expect(apiMocks.preview).toHaveBeenCalledWith({
      dataset_version: populatedCatalog.dataset_version,
      unit_paths: ['objects/sha256/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json'],
      record_ids: ['mentor_example0001'],
    });
    fireEvent.click(screen.getByRole('button', { name: '确认导入 1 位' }));

    await waitFor(() => expect(apiMocks.importRecords).toHaveBeenCalledTimes(1));
    expect(apiMocks.importRecords).toHaveBeenCalledWith({
      dataset_version: populatedCatalog.dataset_version,
      unit_paths: ['objects/sha256/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json'],
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

  it('opens GitHub only for explicit error feedback and prefills community data', async () => {
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);
    apiMocks.listRecords.mockResolvedValue(recordsPayload);

    renderPage();
    fireEvent.click(await screen.findByLabelText(/计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '查看导师' }));
    await screen.findByText('zhang@example.edu');
    fireEvent.click(screen.getByRole('button', { name: /反馈错误/ }));

    expect(openExternalHttpUrl).toHaveBeenCalledTimes(1);
    const openedUrl = new URL(openExternalHttpUrl.mock.calls[0][0] as string);
    expect(openedUrl.searchParams.get('template')).toBe('report-error.yml');
    expect(openedUrl.searchParams.get('title')).toBe('[信息反馈] 示例大学张老师');
    expect(openedUrl.searchParams.get('record_id')).toBe('mentor_example0001');
    expect(openedUrl.searchParams.get('current_value')).toContain(
      '邮箱：zhang@example.edu',
    );
    expect(navigator.clipboard.writeText).not.toHaveBeenCalled();
    expect(notificationMocks.notifySuccess).toHaveBeenCalledWith(
      '反馈页面已打开',
      '已自动填入导师信息。请选择问题，并补充正确信息和官网来源。',
    );
  });

  it('uses searchable custom filters and renders no native select controls', async () => {
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);
    apiMocks.listRecords.mockResolvedValue(recordsPayload);

    const { container } = renderPage();

    const catalogSchoolFilter = await screen.findByLabelText('学校：全部学校');
    expect(screen.getByLabelText('学院：全部学院')).toBeInTheDocument();
    fireEvent.click(catalogSchoolFilter);
    const catalogSearch = screen.getByLabelText('搜索学校选项');
    fireEvent.change(catalogSearch, { target: { value: '示例' } });
    expect(screen.getByRole('option', { name: '示例大学' })).toBeInTheDocument();
    fireEvent.click(catalogSchoolFilter);

    fireEvent.click(screen.getByLabelText(/选择 示例大学 计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '查看导师' }));
    await screen.findByText('zhang@example.edu');

    expect(screen.getAllByLabelText('学校：全部学校')).toHaveLength(2);
    expect(screen.getAllByLabelText('学院：全部学院')).toHaveLength(2);
    expect(screen.getByLabelText('系所：全部系所')).toBeInTheDocument();
    expect(screen.getByLabelText('职称：全部职称')).toBeInTheDocument();
    expect(screen.getByLabelText('本地状态：全部情况')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /搜索范围：选择字段：全部字段/ }),
    ).toBeInTheDocument();
    expect(screen.getByTestId('community-mentor-keyword-control')).toHaveClass(
      'ui-select-shell',
      'h-10',
    );
    expect(container.querySelector('select')).toBeNull();
    expect(container.querySelector('input[type="checkbox"]')).toBeNull();
  });

  it('selects and clears all colleges in the current filter', async () => {
    const secondPath = 'objects/sha256/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.json';
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

    renderPage();

    const selectFiltered = await screen.findByRole('button', {
      name: '全选当前学院',
    });
    fireEvent.click(selectFiltered);
    expect(selectFiltered).toHaveAttribute('aria-pressed', 'true');
    expect(within(selectFiltered).getByText(/已选 2\/2/)).toBeInTheDocument();

    fireEvent.click(selectFiltered);
    expect(selectFiltered).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByText(/已选 0 个学院/)).toBeInTheDocument();
  });

  it('shows nine colleges per page by default and uses the shared pagination controls', async () => {
    const appHeader = document.createElement('nav');
    appHeader.dataset.appHeader = 'true';
    vi.spyOn(appHeader, 'getBoundingClientRect').mockReturnValue({
      bottom: 128,
    } as DOMRect);
    document.body.append(appHeader);
    const units = Array.from({ length: 10 }, (_, index) => ({
      id: `org_example_school_${index + 1}`,
      name: `学院${String(index + 1).padStart(2, '0')}`,
      type: 'school' as const,
      record_count: 1,
      path: `objects/sha256/${String(index + 1).padStart(64, '0')}.json`,
    }));
    apiMocks.getCatalog.mockResolvedValue({
      ...populatedCatalog,
      record_count: units.length,
      universities: populatedCatalog.universities.map((university) => ({
        ...university,
        record_count: units.length,
        units,
      })),
    });

    renderPage();

    const pagination = await screen.findByRole('navigation', {
      name: '学校与学院分页',
    });
    expect(pagination.parentElement).toHaveClass('lg:flex-row', 'lg:items-center');
    expect(
      within(pagination.parentElement as HTMLElement).getByRole('button', {
        name: '查看导师',
      }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('学校与学院每页数量')).toHaveTextContent('9');
    expect(screen.getByLabelText(/选择 示例大学 学院01/)).toBeInTheDocument();
    expect(screen.getByLabelText(/选择 示例大学 学院09/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/选择 示例大学 学院10/)).not.toBeInTheDocument();
    expect(within(pagination).getByText('显示 1-9 / 10 个学院')).toBeInTheDocument();

    fireEvent.click(within(pagination).getByRole('button', { name: '下一页' }));

    expect(screen.queryByLabelText(/选择 示例大学 学院01/)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/选择 示例大学 学院10/)).toBeInTheDocument();
    expect(within(pagination).getByText('显示 10-10 / 10 个学院')).toBeInTheDocument();
    const selector = screen.getByTestId('community-mentor-unit-selector');
    expect(selector).toHaveFocus();
    expect(selector.style.scrollMarginTop).toBe('144px');
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'auto', block: 'start' });
    appHeader.remove();
  });

  it('opens a read-only detail dialog and links contributors to GitHub', async () => {
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);
    apiMocks.listRecords.mockResolvedValue(recordsPayload);

    renderPage();
    fireEvent.click(await screen.findByLabelText(/选择 示例大学 计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '查看导师' }));
    await screen.findByText('zhang@example.edu');

    expect(screen.getByRole('link', { name: '@example-user' })).toHaveAttribute(
      'href',
      'https://github.com/example-user',
    );
    fireEvent.click(screen.getByRole('button', { name: /查看详情/ }));

    const detail = await screen.findByRole('dialog', { name: '导师详情：张老师' });
    expect(detail).toHaveTextContent('Example Paper');
    expect(detail).toHaveTextContent('代表论文');
    expect(detail).not.toHaveTextContent('保存');
    fireEvent.click(screen.getByRole('button', { name: '关闭导师详情' }));
    expect(screen.queryByRole('dialog', { name: '导师详情：张老师' })).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole('button', { name: '查看导师' }));
    await screen.findByText('张老师');
    const searchInput = screen.getByLabelText('搜索导师');

    fireEvent.change(searchInput, { target: { value: '不存在的关键词' } });
    expect(screen.queryByText('张老师')).not.toBeInTheDocument();
    fireEvent.change(searchInput, { target: { value: 'alternate@example.edu' } });
    expect(screen.getByText('张老师')).toBeInTheDocument();
    fireEvent.change(searchInput, { target: { value: '访问教授' } });
    expect(screen.getByText('张老师')).toBeInTheDocument();

    fireEvent.change(searchInput, { target: { value: 'alternate@example.edu' } });
    fireEvent.click(
      screen.getByRole('button', { name: /搜索范围：选择字段：全部字段/ }),
    );
    fireEvent.click(screen.getByRole('button', { name: '全部取消' }));
    fireEvent.click(screen.getByRole('option', { name: '姓名' }));
    expect(screen.queryByText('张老师')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('option', { name: '邮箱' }));
    expect(screen.getByText('张老师')).toBeInTheDocument();
  });

  it('paginates results and can select every record across pages', async () => {
    const comparisons = Array.from({ length: 101 }, (_, index) => buildComparison(index));
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
    fireEvent.click(screen.getByRole('button', { name: '查看导师' }));
    expect(await screen.findByText('导师0001')).toBeInTheDocument();
    expect(screen.queryByText('导师0101')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findByText('导师0101')).toBeInTheDocument();

    const recordList = screen.getByTestId('community-mentor-record-list');
    fireEvent.click(screen.getByRole('button', { name: '全选当前导师' }));
    expect(recordList).toHaveClass('is-bulk-selecting');
    await waitFor(() => {
      const selectAll = screen.getByRole('button', { name: '取消全选导师' });
      expect(selectAll).toHaveAttribute('aria-pressed', 'true');
    });
    expect(screen.getByText(/已选 101\/101/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消全选导师' }));
    await waitFor(() => {
      const selectAll = screen.getByRole('button', { name: '全选当前导师' });
      expect(selectAll).toHaveAttribute('aria-pressed', 'false');
    });
    await waitFor(() => expect(recordList).not.toHaveClass('is-bulk-selecting'));
    expect(notificationMocks.notifyWarning).not.toHaveBeenCalledWith(
      expect.stringContaining('已选择前'),
      expect.anything(),
    );
  }, 10_000);

  it('paginates large import previews instead of rendering every selected mentor', async () => {
    const comparisons = Array.from({ length: 30 }, (_, index) => buildComparison(index));
    const largePayload = { ...recordsPayload, records: comparisons };
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
    apiMocks.listRecords.mockResolvedValue(largePayload);
    apiMocks.preview.mockResolvedValue(largePayload);

    renderPage();
    fireEvent.click(await screen.findByLabelText(/计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '查看导师' }));
    await screen.findByText('导师0001');
    fireEvent.click(screen.getByRole('button', { name: '全选当前导师' }));
    fireEvent.click(screen.getByRole('button', { name: /预览并导入 30/ }));

    const preview = await screen.findByRole('dialog', { name: '社区导师导入预览' });
    expect(within(preview).getByText('导师0001')).toBeInTheDocument();
    expect(within(preview).queryByText('导师0026')).not.toBeInTheDocument();
    expect(within(preview).getByText(/1\/2 页 · 1–25 \/ 30 位/)).toBeInTheDocument();

    fireEvent.click(within(preview).getByRole('button', { name: '下一页导入预览' }));
    expect(within(preview).queryByText('导师0001')).not.toBeInTheDocument();
    expect(within(preview).getByText('导师0026')).toBeInTheDocument();
    expect(within(preview).getByText(/2\/2 页 · 26–30 \/ 30 位/)).toBeInTheDocument();

    fireEvent.change(within(preview).getByLabelText('搜索导入预览导师'), {
      target: { value: '导师0030' },
    });
    expect(within(preview).getByText('导师0030')).toBeInTheDocument();
    expect(
      within(preview).queryByRole('button', { name: '下一页导入预览' }),
    ).not.toBeInTheDocument();
  });

  it('filters preview records by field state and involved field', async () => {
    const titleConflict: CommunityMentorComparisonDTO = {
      ...buildComparison(0),
      category: 'conflict',
      local_professor_id: 1,
      local_professor_name: '导师甲',
      record: {
        ...buildComparison(0).record,
        name: '导师甲',
      },
      fields: [
        {
          field: 'title',
          label: '职称',
          local_value: '副教授',
          community_value: '教授',
          baseline_present: false,
          baseline_value: null,
          state: 'conflict',
          suggested_choice: 'local',
        },
      ],
    };
    const papersUpdate: CommunityMentorComparisonDTO = {
      ...buildComparison(1),
      category: 'remote_modified',
      local_professor_id: 2,
      local_professor_name: '导师乙',
      record: {
        ...buildComparison(1).record,
        name: '导师乙',
      },
      fields: [
        {
          field: 'recent_papers',
          label: '近期论文',
          local_value: ['旧论文'],
          community_value: ['新论文'],
          baseline_present: true,
          baseline_value: ['旧论文'],
          state: 'remote_modified',
          suggested_choice: 'community',
        },
      ],
    };
    const payload = { ...recordsPayload, records: [titleConflict, papersUpdate] };
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);
    apiMocks.listRecords.mockResolvedValue(payload);
    apiMocks.preview.mockResolvedValue(payload);

    renderPage();
    fireEvent.click(await screen.findByLabelText(/计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '查看导师' }));
    await screen.findByText('导师甲');
    fireEvent.click(screen.getByRole('button', { name: '全选当前导师' }));
    fireEvent.click(screen.getByRole('button', { name: /预览并导入 2/ }));

    const preview = await screen.findByRole('dialog', { name: '社区导师导入预览' });
    fireEvent.change(within(preview).getByLabelText('搜索导入预览导师'), {
      target: { value: '副教授' },
    });
    expect(within(preview).getByText('导师甲')).toBeInTheDocument();
    expect(within(preview).queryByText('导师乙')).not.toBeInTheDocument();
    fireEvent.click(within(preview).getByRole('button', { name: '清除全部筛选' }));

    fireEvent.click(within(preview).getByLabelText('差异类型：全部差异'));
    fireEvent.click(within(preview).getByRole('button', { name: '取消全选' }));
    fireEvent.click(within(preview).getByRole('option', { name: '内容不同' }));
    fireEvent.click(within(preview).getByRole('button', { name: '应用' }));

    expect(within(preview).getByText('导师甲')).toBeInTheDocument();
    expect(within(preview).queryByText('导师乙')).not.toBeInTheDocument();

    fireEvent.click(within(preview).getByRole('button', { name: '清除全部筛选' }));
    fireEvent.click(within(preview).getByRole('button', {
      name: '只看涉及近期论文的导师',
    }));
    expect(within(preview).queryByText('导师甲')).not.toBeInTheDocument();
    expect(within(preview).getByText('导师乙')).toBeInTheDocument();
  });

  it('applies one field choice only to mentors in the current preview filter', async () => {
    const first: CommunityMentorComparisonDTO = {
      ...buildComparison(0),
      category: 'conflict',
      local_professor_id: 1,
      local_professor_name: '导师甲',
      record: { ...buildComparison(0).record, name: '导师甲' },
      fields: [
        {
          field: 'title',
          label: '职称',
          local_value: '副教授',
          community_value: '教授',
          baseline_present: false,
          baseline_value: null,
          state: 'conflict',
          suggested_choice: 'local',
        },
        {
          field: 'research_direction',
          label: '研究方向',
          local_value: '本地方向',
          community_value: '社区方向',
          baseline_present: false,
          baseline_value: null,
          state: 'conflict',
          suggested_choice: 'local',
        },
      ],
    };
    const second: CommunityMentorComparisonDTO = {
      ...first,
      comparison_token: 'b'.repeat(64),
      local_professor_id: 2,
      local_professor_name: '导师乙',
      record: {
        ...first.record,
        id: 'mentor_second',
        name: '导师乙',
        email: 'second@example.edu',
      },
    };
    const payload = { ...recordsPayload, records: [first, second] };
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);
    apiMocks.listRecords.mockResolvedValue(payload);
    apiMocks.preview.mockResolvedValue(payload);

    renderPage();
    fireEvent.click(await screen.findByLabelText(/计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '查看导师' }));
    await screen.findByText('导师甲');
    fireEvent.click(screen.getByRole('button', { name: '全选当前导师' }));
    fireEvent.click(screen.getByRole('button', { name: /预览并导入 2/ }));

    const preview = await screen.findByRole('dialog', { name: '社区导师导入预览' });
    fireEvent.change(within(preview).getByLabelText('搜索导入预览导师'), {
      target: { value: '导师甲' },
    });
    fireEvent.click(within(preview).getByRole('button', {
      name: '当前筛选的职称全部采用社区',
    }));
    fireEvent.click(within(preview).getByRole('button', { name: '清除全部筛选' }));

    const firstSection = within(preview).getByText('导师甲').closest('section');
    const secondSection = within(preview).getByText('导师乙').closest('section');
    expect(firstSection).not.toBeNull();
    expect(secondSection).not.toBeNull();
    expect(within(firstSection!).getByRole('button', { name: '采用社区职称' }))
      .toHaveAttribute('aria-pressed', 'true');
    expect(within(firstSection!).getByRole('button', { name: '采用社区研究方向' }))
      .toHaveAttribute('aria-pressed', 'false');
    expect(within(secondSection!).getByRole('button', { name: '采用社区职称' }))
      .toHaveAttribute('aria-pressed', 'false');
  });

  it('applies a filtered field choice to matching mentors on every preview page', async () => {
    const comparisons = Array.from({ length: 30 }, (_, index) => {
      const item = buildComparison(index);
      return {
        ...item,
        category: 'conflict' as const,
        local_professor_id: index + 1,
        local_professor_name: item.record.name,
        fields: [
          {
            field: 'title',
            label: '职称',
            local_value: '副教授',
            community_value: '教授',
            baseline_present: false,
            baseline_value: null,
            state: 'conflict' as const,
            suggested_choice: 'local' as const,
          },
        ],
      };
    });
    const payload = { ...recordsPayload, records: comparisons };
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);
    apiMocks.listRecords.mockResolvedValue(payload);
    apiMocks.preview.mockResolvedValue(payload);

    renderPage();
    fireEvent.click(await screen.findByLabelText(/计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '查看导师' }));
    await screen.findByText('导师0001');
    fireEvent.click(screen.getByRole('button', { name: '全选当前导师' }));
    fireEvent.click(screen.getByRole('button', { name: /预览并导入 30/ }));

    const preview = await screen.findByRole('dialog', { name: '社区导师导入预览' });
    fireEvent.click(within(preview).getByRole('button', {
      name: '当前筛选的职称全部采用社区',
    }));
    fireEvent.click(within(preview).getByRole('button', { name: '下一页导入预览' }));

    const secondPageSection = within(preview).getByText('导师0026').closest('section');
    expect(secondPageSection).not.toBeNull();
    expect(within(secondPageSection!).getByRole('button', { name: '采用社区职称' }))
      .toHaveAttribute('aria-pressed', 'true');
  });

  it('reveals a hidden unconfirmed identity instead of importing it', async () => {
    const hiddenConflictBase = buildComparison(0);
    const hiddenConflict: CommunityMentorComparisonDTO = {
      ...hiddenConflictBase,
      category: 'conflict',
      local_professor_id: 1,
      local_professor_name: '待确认导师',
      identity_conflict: true,
      match_reason: '姓名相同，但邮箱不同',
      record: {
        ...hiddenConflictBase.record,
        name: '待确认导师',
      },
    };
    const visibleRecordBase = buildComparison(1);
    const visibleRecord: CommunityMentorComparisonDTO = {
      ...visibleRecordBase,
      record: {
        ...visibleRecordBase.record,
        name: '筛选中的导师',
      },
    };
    const payload = { ...recordsPayload, records: [hiddenConflict, visibleRecord] };
    apiMocks.getCatalog.mockResolvedValue(populatedCatalog);
    apiMocks.listRecords.mockResolvedValue(payload);
    apiMocks.preview.mockResolvedValue(payload);

    renderPage();
    fireEvent.click(await screen.findByLabelText(/计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '查看导师' }));
    await screen.findByText('待确认导师');
    fireEvent.click(screen.getByRole('button', { name: '全选当前导师' }));
    fireEvent.click(screen.getByRole('button', { name: /预览并导入 2/ }));

    const preview = await screen.findByRole('dialog', { name: '社区导师导入预览' });
    fireEvent.change(within(preview).getByLabelText('搜索导入预览导师'), {
      target: { value: '筛选中的导师' },
    });
    expect(within(preview).queryByText('待确认导师')).not.toBeInTheDocument();

    fireEvent.click(within(preview).getByRole('button', { name: '确认导入 2 位' }));

    expect(await within(preview).findByText('待确认导师')).toBeInTheDocument();
    expect(within(preview).getByLabelText('只看待确认导师'))
      .toHaveAttribute('aria-pressed', 'true');
    expect(notificationMocks.notifyWarning).toHaveBeenCalledWith(
      '请确认导师身份',
      '“待确认导师”存在姓名或学校冲突。',
    );
    expect(apiMocks.importRecords).not.toHaveBeenCalled();
  });

  it('allows an explicit community empty value while keeping it local by default', async () => {
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
    apiMocks.importRecords.mockResolvedValue({
      inserted_count: 0,
      updated_count: 1,
      linked_count: 0,
      skipped_count: 0,
      message: '社区导入完成',
      professors: [],
    });

    renderPage();
    fireEvent.click(await screen.findByLabelText(/计算机学院/));
    fireEvent.click(screen.getByRole('button', { name: '查看导师' }));
    await screen.findByText('zhang@example.edu');
    fireEvent.click(screen.getByLabelText('选择 张老师'));
    fireEvent.click(screen.getByRole('button', { name: /预览并导入 1/ }));

    const communityChoice = await screen.findByRole('button', { name: '采用社区系所' });
    const localChoice = screen.getByRole('button', { name: /^保留本地/ });
    expect(communityChoice).toBeEnabled();
    expect(communityChoice).toHaveAttribute('aria-pressed', 'false');
    expect(localChoice).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('空值（将清空本地内容）')).toBeInTheDocument();
    await waitFor(() => expect(document.body.style.overflow).toBe('hidden'));

    fireEvent.click(screen.getByRole('button', { name: '全部采用社区' }));
    expect(communityChoice).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: '全部保留本地' }));
    expect(localChoice).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: '全部采用社区' }));
    fireEvent.click(screen.getByRole('button', { name: '确认导入 1 位' }));

    await waitFor(() => expect(apiMocks.importRecords).toHaveBeenCalledTimes(1));
    expect(apiMocks.importRecords.mock.calls[0][0]).toMatchObject({
      items: [
        {
          community_record_id: localOnlyComparison.record.id,
          field_choices: { department: 'community' },
        },
      ],
    });
    await waitFor(() => expect(document.body.style.overflow).toBe(''));
  });

  it('selects at most 2000 mentors from the current filter and reports the remainder', () => {
    const visibleRecordIds = Array.from(
      { length: 2001 },
      (_, index) => `mentor_example${String(index).padStart(4, '0')}`,
    );

    const result = addVisibleRecordSelection([], visibleRecordIds);

    expect(result.recordIds).toHaveLength(2000);
    expect(result.recordIds).toEqual(visibleRecordIds.slice(0, 2000));
    expect(result.omittedCount).toBe(1);
    expect(
      getVisibleRecordSelectionState(result.recordIds, visibleRecordIds),
    ).toEqual({
      selectedVisibleCount: 2000,
      allVisibleSelected: false,
      partiallyVisibleSelected: true,
    });
  });

  it('adds filtered colleges in order while respecting both selection limits', () => {
    const allUnits = Array.from({ length: 22 }, (_, index) => ({
      id: `unit-${index + 1}`,
      recordCount: index === 18 ? 1_900 : 10,
    }));

    const result = addFilteredCommunityUnitSelection([], allUnits, allUnits);

    expect(result.unitIds).toEqual([
      ...Array.from({ length: 18 }, (_, index) => `unit-${index + 1}`),
      'unit-20',
      'unit-21',
    ]);
    expect(result.selectedRecordCount).toBe(200);
    expect(result.omittedByRecordLimit).toBe(1);
    expect(result.omittedByUnitLimit).toBe(1);
  });

  it('keeps the loaded list but disables preview when the selected units change', async () => {
    const secondPath = 'objects/sha256/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.json';
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
    fireEvent.click(screen.getByRole('button', { name: '查看导师' }));
    await screen.findByText('zhang@example.edu');
    fireEvent.click(screen.getByLabelText('选择 张老师'));
    fireEvent.click(screen.getByLabelText(/人工智能研究院/));

    expect(
      await screen.findByText(/学院选择已变化，请重新加载导师/),
    ).toBeInTheDocument();
    expect(screen.getByText(/已选 1 位/)).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole('button', { name: '查看导师' }));
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
