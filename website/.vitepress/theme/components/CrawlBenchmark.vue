<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { withBase } from "vitepress";
import BenchmarkFilterSelect from "./BenchmarkFilterSelect.vue";
import {
  benchmarkTargetKey,
  buildPaginationPages,
  buildBenchmarkSummary,
  buildBenchmarkVersionStats,
  buildLatestBenchmarkRecords,
  crawlBenchmarkPayload,
  formatBenchmarkDate,
  formatBenchmarkNumber,
  formatCoverage,
  formatDuration,
  groupBenchmarkHistory,
  recordCoverage,
  type BenchmarkPublicStatus,
  type CrawlBenchmarkRecord,
} from "../crawlBenchmark";

const searchQuery = ref("");
const universityFilter = ref("all");
const versionFilter = ref("all");
const statusFilter = ref<"all" | BenchmarkPublicStatus>("all");
const currentPage = ref(1);
const pageSize = 8;

const latestRecords = buildLatestBenchmarkRecords(crawlBenchmarkPayload.records);
const historyByTarget = groupBenchmarkHistory(crawlBenchmarkPayload.records);
const summary = buildBenchmarkSummary(latestRecords);
const versionStats = buildBenchmarkVersionStats(crawlBenchmarkPayload.records);

const universities = [...new Set(latestRecords.map((record) => record.university))].sort((a, b) =>
  a.localeCompare(b, "zh-CN"),
);
const versions = [
  ...new Set(latestRecords.map((record) => record.appVersion).filter(Boolean) as string[]),
].sort((a, b) => b.localeCompare(a, undefined, { numeric: true }));
const universityOptions = [
  { value: "all", label: "全部高校" },
  ...universities.map((university) => ({ value: university, label: university })),
];
const versionOptions = [
  { value: "all", label: "全部版本" },
  ...versions.map((version) => ({ value: version, label: `v${version}` })),
];
const statusOptions = [
  { value: "all", label: "全部状态" },
  { value: "verified", label: "已实测" },
  { value: "adapting", label: "正在适配" },
];

const filteredRecords = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase("zh-CN");
  return latestRecords.filter((record) => {
    const matchesSearch =
      !query ||
      `${record.university} ${record.school}`.toLocaleLowerCase("zh-CN").includes(query);
    const matchesUniversity =
      universityFilter.value === "all" || record.university === universityFilter.value;
    const matchesVersion =
      versionFilter.value === "all" || record.appVersion === versionFilter.value;
    const matchesStatus =
      statusFilter.value === "all" || record.publicStatus === statusFilter.value;
    return matchesSearch && matchesUniversity && matchesVersion && matchesStatus;
  });
});
const totalPages = computed(() =>
  Math.max(1, Math.ceil(filteredRecords.value.length / pageSize)),
);
const paginatedRecords = computed(() => {
  const start = (currentPage.value - 1) * pageSize;
  return filteredRecords.value.slice(start, start + pageSize);
});
const paginationPages = computed(() => {
  return buildPaginationPages(currentPage.value, totalPages.value);
});

watch(
  [searchQuery, universityFilter, versionFilter, statusFilter],
  () => {
    currentPage.value = 1;
  },
);

watch(totalPages, (pageCount) => {
  if (currentPage.value > pageCount) currentPage.value = pageCount;
});

function coverage(record: CrawlBenchmarkRecord, count: number): number | null {
  return recordCoverage(count, record.candidateCount);
}

function progressStyle(value: number | null): Record<string, string> {
  return { width: `${Math.round((value ?? 0) * 100)}%` };
}

function historyFor(record: CrawlBenchmarkRecord): CrawlBenchmarkRecord[] {
  return historyByTarget.get(benchmarkTargetKey(record)) ?? [record];
}

function statusLabel(status: BenchmarkPublicStatus): string {
  return status === "verified" ? "已实测" : "正在适配";
}

function versionLabel(record: CrawlBenchmarkRecord): string {
  if (record.appVersion) return `Auto Email Sender v${record.appVersion}`;
  return "早期版本记录";
}

function enrichmentDetailLabel(record: CrawlBenchmarkRecord): string | null {
  if (record.enrichmentSucceededCount === null) return null;
  const details = [
    `已发起 ${formatBenchmarkNumber(record.enrichmentSelectedCount ?? 0)} 位`,
    `成功 ${formatBenchmarkNumber(record.enrichmentSucceededCount)} 位`,
  ];
  if (record.enrichmentPendingCount) {
    details.push(`进行中 ${formatBenchmarkNumber(record.enrichmentPendingCount)} 位`);
  }
  if (record.enrichmentFailedCount) {
    details.push(`失败 ${formatBenchmarkNumber(record.enrichmentFailedCount)} 位`);
  }
  return details.join("，");
}

function goToPage(page: number): void {
  currentPage.value = Math.min(totalPages.value, Math.max(1, page));
  requestAnimationFrame(() => {
    document.querySelector(".benchmark-result-count")?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  });
}
</script>

<template>
  <main class="benchmark-page">
    <section class="benchmark-hero">
      <div class="benchmark-hero-copy">
        <span class="benchmark-eyebrow"><i></i> 真实高校官网 · 持续更新</span>
        <h1>智能抓取效果展示</h1>
        <p>
          我们将不同高校官网的实际抓取结果整理为公开汇总，用候选数量、字段覆盖率和处理耗时呈现系统在真实页面中的表现。
        </p>
        <div class="benchmark-hero-actions">
          <a class="benchmark-primary-action" :href="withBase('/docs/getting-started')">开始使用</a>
          <a class="benchmark-secondary-action" href="#school-results">查看学校结果</a>
        </div>
        <small>数据更新于 {{ formatBenchmarkDate(crawlBenchmarkPayload.generatedAt) }}</small>
      </div>

      <div class="benchmark-hero-visual" aria-label="字段覆盖率概览">
        <div class="hero-visual-header">
          <span>综合字段覆盖</span>
          <strong>{{ summary.verifiedTargetCount }} 个目标已实测</strong>
        </div>
        <div class="hero-coverage-item">
          <div><span>邮箱</span><strong>{{ formatCoverage(summary.emailCoverage) }}</strong></div>
          <div class="hero-coverage-track"><i :style="progressStyle(summary.emailCoverage)"></i></div>
        </div>
        <div class="hero-coverage-item">
          <div><span>研究方向</span><strong>{{ formatCoverage(summary.researchDirectionCoverage) }}</strong></div>
          <div class="hero-coverage-track research"><i :style="progressStyle(summary.researchDirectionCoverage)"></i></div>
        </div>
        <div class="hero-coverage-item">
          <div><span>职称</span><strong>{{ formatCoverage(summary.titleCoverage) }}</strong></div>
          <div class="hero-coverage-track title"><i :style="progressStyle(summary.titleCoverage)"></i></div>
        </div>
        <p>覆盖率表示成功提取到非空字段的比例。</p>
      </div>
    </section>

    <section class="benchmark-kpis" aria-label="实测数据总览">
      <article>
        <span>覆盖高校</span>
        <strong>{{ summary.universityCount }}</strong>
        <small>来自真实学校官网</small>
      </article>
      <article>
        <span>学院与研究机构</span>
        <strong>{{ summary.targetCount }}</strong>
        <small>默认展示最新测试</small>
      </article>
      <article>
        <span>识别候选导师</span>
        <strong>{{ formatBenchmarkNumber(summary.candidateCount) }}</strong>
        <small>按最新结果汇总</small>
      </article>
      <article>
        <span>邮箱字段覆盖</span>
        <strong>{{ formatCoverage(summary.emailCoverage) }}</strong>
        <small>候选记录中的非空比例</small>
      </article>
    </section>

    <section v-if="versionStats.length" class="benchmark-version-section">
      <div class="benchmark-section-heading">
        <div>
          <span>版本观察</span>
          <h2>持续记录每次迭代的真实表现</h2>
        </div>
        <p>版本统计仅使用明确记录了软件版本的历史运行；新任务会自动保存版本快照。</p>
      </div>
      <div class="version-card-grid">
        <article v-for="version in versionStats" :key="version.version" class="version-card">
          <header>
            <div><span>Auto Email Sender</span><strong>v{{ version.version }}</strong></div>
            <small>{{ version.recordCount }} 次实测</small>
          </header>
          <div class="version-candidate-count">
            {{ formatBenchmarkNumber(version.candidateCount) }}
            <span>位候选导师</span>
          </div>
          <div class="version-metrics">
            <div><span>邮箱</span><strong>{{ formatCoverage(version.emailCoverage) }}</strong></div>
            <div><span>职称</span><strong>{{ formatCoverage(version.titleCoverage) }}</strong></div>
            <div><span>方向</span><strong>{{ formatCoverage(version.researchDirectionCoverage) }}</strong></div>
          </div>
        </article>
      </div>
    </section>

    <section id="school-results" class="benchmark-results-section">
      <div class="benchmark-section-heading results-heading">
        <div>
          <span>学校实测</span>
          <h2>查看每个学院的最新抓取情况</h2>
        </div>
        <p>点击官网入口可核对测试来源；有多次记录的学院可展开查看历史变化。</p>
      </div>

      <div class="benchmark-toolbar" role="search">
        <label class="benchmark-search">
          <span class="sr-only">搜索学校或学院</span>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="11" cy="11" r="7"></circle>
            <path d="m16 16 4 4"></path>
          </svg>
          <input v-model="searchQuery" type="search" placeholder="搜索学校或学院" />
        </label>
        <BenchmarkFilterSelect
          v-model="universityFilter"
          label="高校"
          :options="universityOptions"
        />
        <BenchmarkFilterSelect
          v-model="versionFilter"
          label="版本"
          :options="versionOptions"
        />
        <BenchmarkFilterSelect
          v-model="statusFilter"
          label="状态"
          :options="statusOptions"
        />
      </div>

      <div class="benchmark-result-count">
        <span>共 <strong>{{ filteredRecords.length }}</strong> 个学院或研究机构</span>
        <span v-if="filteredRecords.length">第 {{ currentPage }} / {{ totalPages }} 页</span>
      </div>

      <div v-if="filteredRecords.length" class="school-card-list">
        <article v-for="record in paginatedRecords" :key="record.recordId" class="school-card">
          <div class="school-identity">
            <header class="school-card-header">
              <div>
                <span class="school-university">{{ record.university }}</span>
                <h3>{{ record.school }}</h3>
              </div>
              <span class="status-pill" :class="record.publicStatus">
                <i></i>{{ statusLabel(record.publicStatus) }}
              </span>
            </header>

            <div class="school-meta">
              <span>{{ versionLabel(record) }}</span>
              <span>{{ formatBenchmarkDate(record.testedAt) }}</span>
              <a :href="record.startUrl" target="_blank" rel="noopener noreferrer">官网入口 ↗</a>
            </div>
          </div>

          <div class="candidate-highlight">
            <span>候选导师</span>
            <strong>{{ formatBenchmarkNumber(record.candidateCount) }}</strong>
            <small
              v-if="record.pageCount !== null || record.enrichmentSucceededCount !== null"
            >
              <span v-if="record.pageCount !== null">处理 {{ record.pageCount }} 个页面</span>
              <template v-if="record.pageCount !== null && record.enrichmentSucceededCount !== null"> · </template>
              <span
                v-if="record.enrichmentSucceededCount !== null"
                :title="enrichmentDetailLabel(record) ?? undefined"
              >
                补全 {{ formatBenchmarkNumber(record.enrichmentSucceededCount) }}/{{ formatBenchmarkNumber(record.candidateCount) }}
              </span>
            </small>
          </div>

          <div class="school-coverage-list">
            <div>
              <div>
                <span>邮箱</span>
                <strong>{{ formatCoverage(coverage(record, record.emailCount)) }}</strong>
              </div>
              <div class="school-progress"><i :style="progressStyle(coverage(record, record.emailCount))"></i></div>
            </div>
            <div>
              <div>
                <span>职称</span>
                <strong>{{ formatCoverage(coverage(record, record.titleCount)) }}</strong>
              </div>
              <div class="school-progress title"><i :style="progressStyle(coverage(record, record.titleCount))"></i></div>
            </div>
            <div>
              <div>
                <span>研究方向</span>
                <strong>{{ formatCoverage(coverage(record, record.researchDirectionCount)) }}</strong>
              </div>
              <div class="school-progress research"><i :style="progressStyle(coverage(record, record.researchDirectionCount))"></i></div>
            </div>
          </div>

          <footer class="school-card-footer">
            <span><small>耗时</small><strong>{{ formatDuration(record.durationSeconds) }}</strong></span>
            <span v-if="record.modelName"><small>模型</small><strong>{{ record.modelName }}</strong></span>
          </footer>

          <details v-if="historyFor(record).length > 1" class="school-history">
            <summary>{{ historyFor(record).length }} 次历史实测</summary>
            <div class="history-list">
              <div v-for="history in historyFor(record)" :key="history.recordId">
                <span>{{ history.appVersion ? `v${history.appVersion}` : formatBenchmarkDate(history.testedAt) }}</span>
                <strong>{{ history.candidateCount }} 位</strong>
                <small>邮箱 {{ formatCoverage(coverage(history, history.emailCount)) }}</small>
              </div>
            </div>
          </details>
        </article>
      </div>

      <nav
        v-if="filteredRecords.length && totalPages > 1"
        class="benchmark-pagination"
        aria-label="学校实测分页"
      >
        <button
          type="button"
          :disabled="currentPage === 1"
          @click="goToPage(currentPage - 1)"
        >
          ← 上一页
        </button>
        <div>
          <button
            v-for="page in paginationPages"
            :key="page"
            type="button"
            :class="{ active: page === currentPage }"
            :aria-current="page === currentPage ? 'page' : undefined"
            @click="goToPage(page)"
          >
            {{ page }}
          </button>
        </div>
        <button
          type="button"
          :disabled="currentPage === totalPages"
          @click="goToPage(currentPage + 1)"
        >
          下一页 →
        </button>
      </nav>

      <div v-if="!filteredRecords.length" class="benchmark-empty-state">
        <strong>没有找到符合条件的学校</strong>
        <span>可以清空搜索内容或调整筛选条件。</span>
      </div>
    </section>

    <section class="benchmark-methodology">
      <div>
        <span>如何理解这些数据</span>
        <h2>数据来源和统计说明</h2>
      </div>
      <div class="methodology-grid">
        <article>
          <strong>真实页面</strong>
          <p>每条记录都对应公开的高校官网入口，不使用专门为演示准备的页面。</p>
        </article>
        <article>
          <strong>覆盖率不是准确率</strong>
          <p>{{ crawlBenchmarkPayload.methodology.coverageDefinition }}</p>
        </article>
        <article>
          <strong>只公开汇总</strong>
          <p>{{ crawlBenchmarkPayload.methodology.privacy }}</p>
        </article>
      </div>
    </section>
  </main>
</template>

<style scoped>
.benchmark-page {
  --benchmark-ink: #18181b;
  --benchmark-muted: #667085;
  --benchmark-border: rgba(24, 24, 27, 0.12);
  --benchmark-paper: #ffffff;
  --benchmark-soft: #f6f7f9;
  --benchmark-red: #b91c1c;
  --benchmark-red-bright: #dc2626;
  --benchmark-amber: #d97706;
  margin: 0 auto;
  max-width: 1200px;
  padding: 48px 28px 80px;
  color: var(--benchmark-ink);
}

.benchmark-hero {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(340px, 0.75fr);
  gap: 52px;
  border-bottom: 1px solid var(--benchmark-border);
  padding: 64px 0 56px;
}

.benchmark-hero-copy {
  position: relative;
  z-index: 1;
}

.benchmark-eyebrow,
.benchmark-section-heading > div > span,
.benchmark-methodology > div:first-child > span {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  color: var(--benchmark-red);
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0;
}

.benchmark-eyebrow i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #ef4444;
  box-shadow: 0 0 0 5px rgba(239, 68, 68, 0.12);
}

.benchmark-hero h1 {
  max-width: 620px;
  margin: 22px 0 0;
  color: var(--benchmark-ink);
  font-size: 62px;
  font-weight: 850;
  letter-spacing: 0;
  line-height: 1.04;
}

.benchmark-hero-copy > p {
  max-width: 650px;
  margin: 24px 0 0;
  color: var(--benchmark-muted);
  font-size: 17px;
  line-height: 1.85;
}

.benchmark-hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 32px;
}

.benchmark-hero-actions a {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 44px;
  border-radius: 7px;
  padding: 0 23px;
  font-size: 14px;
  font-weight: 750;
  text-decoration: none;
  transition: transform 160ms ease, box-shadow 160ms ease;
}

.benchmark-hero-actions a:hover {
  transform: translateY(-2px);
}

.benchmark-primary-action {
  color: #fff !important;
  background: #c91f24;
  box-shadow: 0 12px 28px rgba(185, 28, 28, 0.26);
}

.benchmark-secondary-action {
  border: 1px solid rgba(127, 29, 29, 0.18);
  color: #7f1d1d !important;
  background: #ffffff;
}

.benchmark-hero-copy > small {
  display: block;
  margin-top: 22px;
  color: var(--benchmark-muted);
  font-size: 12px;
}

.benchmark-hero-visual {
  position: relative;
  z-index: 1;
  align-self: center;
  border: 1px solid var(--benchmark-border);
  border-radius: 8px;
  padding: 28px;
  background: #ffffff;
  box-shadow: 0 16px 36px rgba(15, 23, 42, 0.08);
}

.hero-visual-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid var(--benchmark-border);
  padding-bottom: 20px;
}

.hero-visual-header span {
  color: var(--benchmark-muted);
  font-size: 13px;
}

.hero-visual-header strong {
  color: var(--benchmark-red);
  font-size: 13px;
}

.hero-coverage-item {
  margin-top: 22px;
}

.hero-coverage-item > div:first-child,
.school-coverage-list > div > div:first-child {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.hero-coverage-item span {
  color: var(--benchmark-muted);
  font-size: 14px;
}

.hero-coverage-item strong {
  color: var(--benchmark-ink);
  font-size: 18px;
}

.hero-coverage-track,
.school-progress {
  overflow: hidden;
  height: 8px;
  margin-top: 9px;
  border-radius: 999px;
  background: #e4e7ec;
}

.hero-coverage-track i,
.school-progress i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: #dc2626;
}

.hero-coverage-track.research i,
.school-progress.research i {
  background: #2563eb;
}

.hero-coverage-track.title i,
.school-progress.title i {
  background: #16a34a;
}

.benchmark-hero-visual > p {
  margin: 22px 0 0;
  color: var(--benchmark-muted);
  font-size: 12px;
  line-height: 1.6;
}

.benchmark-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-top: 20px;
}

.benchmark-kpis article {
  border: 1px solid var(--benchmark-border);
  border-radius: 8px;
  padding: 24px;
  background: var(--benchmark-paper);
}

.benchmark-kpis span,
.benchmark-kpis small {
  display: block;
  color: var(--benchmark-muted);
  font-size: 12px;
}

.benchmark-kpis strong {
  display: block;
  margin: 8px 0 6px;
  color: var(--benchmark-ink);
  font-size: 34px;
  letter-spacing: 0;
}

.benchmark-version-section,
.benchmark-results-section,
.benchmark-methodology {
  margin-top: 76px;
}

.benchmark-section-heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 40px;
}

.benchmark-section-heading h2,
.benchmark-methodology h2 {
  margin: 10px 0 0;
  color: var(--benchmark-ink);
  font-size: 36px;
  letter-spacing: 0;
  line-height: 1.2;
}

.benchmark-section-heading > p {
  max-width: 470px;
  margin: 0;
  color: var(--benchmark-muted);
  font-size: 13px;
  line-height: 1.75;
}

.version-card-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-top: 28px;
}

.version-card {
  border: 1px solid var(--benchmark-border);
  border-radius: 8px;
  padding: 24px;
  background: var(--benchmark-paper);
}

.version-card header,
.version-card header > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.version-card header > div {
  justify-content: flex-start;
}

.version-card header span,
.version-card header small {
  color: var(--benchmark-muted);
  font-size: 11px;
}

.version-card header strong {
  color: var(--benchmark-red);
  font-size: 14px;
}

.version-candidate-count {
  margin-top: 28px;
  color: var(--benchmark-ink);
  font-size: 34px;
  font-weight: 800;
}

.version-candidate-count span {
  margin-left: 4px;
  color: var(--benchmark-muted);
  font-size: 12px;
  font-weight: 500;
}

.version-metrics {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-top: 22px;
}

.version-metrics > div {
  border-radius: 6px;
  padding: 11px;
  background: var(--benchmark-soft);
}

.version-metrics span,
.version-metrics strong {
  display: block;
}

.version-metrics span {
  color: var(--benchmark-muted);
  font-size: 10px;
}

.version-metrics strong {
  margin-top: 3px;
  color: var(--benchmark-ink);
  font-size: 14px;
}

.benchmark-toolbar {
  display: grid;
  grid-template-columns: minmax(240px, 1.4fr) repeat(3, minmax(150px, 0.6fr));
  gap: 10px;
  margin-top: 28px;
  border: 1px solid var(--benchmark-border);
  border-radius: 8px;
  padding: 12px;
  background: var(--benchmark-soft);
}

.benchmark-toolbar label {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.benchmark-toolbar label > span:not(.sr-only) {
  padding-left: 12px;
  color: var(--benchmark-muted);
  font-size: 10px;
}

.benchmark-toolbar input {
  width: 100%;
  min-height: 42px;
  border: 1px solid transparent;
  border-radius: 6px;
  outline: none;
  padding: 0 12px;
  color: var(--benchmark-ink);
  font: inherit;
  font-size: 13px;
  background: var(--benchmark-paper);
}

.benchmark-toolbar input:focus {
  border-color: rgba(185, 28, 28, 0.42);
  box-shadow: 0 0 0 3px rgba(185, 28, 28, 0.08);
}

.benchmark-search {
  justify-content: end;
}

.benchmark-search svg {
  position: absolute;
  z-index: 1;
  left: 13px;
  bottom: 12px;
  width: 18px;
  height: 18px;
  fill: none;
  stroke: var(--benchmark-muted);
  stroke-width: 1.7;
}

.benchmark-search input {
  padding-left: 39px;
}

.sr-only {
  position: absolute;
  overflow: hidden;
  width: 1px;
  height: 1px;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
}

.benchmark-result-count {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 18px 2px 0;
  color: var(--benchmark-muted);
  font-size: 12px;
}

.benchmark-result-count strong {
  color: var(--benchmark-red);
}

.school-card-list {
  display: grid;
  gap: 10px;
  margin-top: 16px;
}

.school-card {
  display: grid;
  grid-template-columns: minmax(250px, 1.2fr) 116px minmax(330px, 1.4fr) 145px;
  align-items: center;
  gap: 22px;
  border: 1px solid var(--benchmark-border);
  border-radius: 8px;
  padding: 18px 20px;
  background: var(--benchmark-paper);
  transition: border-color 160ms ease, transform 160ms ease, box-shadow 160ms ease;
}

.school-card:hover {
  transform: translateY(-3px);
  border-color: rgba(185, 28, 28, 0.24);
  box-shadow: 0 18px 40px rgba(72, 32, 24, 0.08);
}

.school-identity {
  min-width: 0;
}

.school-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.school-university {
  color: var(--benchmark-red);
  font-size: 12px;
  font-weight: 750;
}

.school-card h3 {
  margin: 4px 0 0;
  color: var(--benchmark-ink);
  font-size: 17px;
  line-height: 1.35;
}

.status-pill {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 7px;
  border-radius: 999px;
  padding: 5px 8px;
  color: #166534;
  font-size: 11px;
  font-weight: 750;
  background: #ecfdf3;
}

.status-pill i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #22c55e;
}

.status-pill.adapting {
  color: #9a3412;
  background: #fff7ed;
}

.status-pill.adapting i {
  background: #f97316;
}

.school-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 12px;
  margin-top: 10px;
  color: var(--benchmark-muted);
  font-size: 11px;
}

.school-meta a {
  color: #9f1239;
  font-weight: 650;
  text-decoration: none;
}

.candidate-highlight {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  border-left: 1px solid var(--benchmark-border);
  padding: 2px 0 2px 18px;
}

.candidate-highlight > span,
.candidate-highlight > small {
  color: var(--benchmark-muted);
  font-size: 11px;
}

.candidate-highlight > strong {
  color: var(--benchmark-red);
  font-size: 30px;
  line-height: 1;
}

.school-coverage-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin: 0;
}

.school-coverage-list span {
  color: var(--benchmark-muted);
  font-size: 12px;
}

.school-coverage-list strong {
  color: var(--benchmark-ink);
  font-size: 13px;
}

.school-progress {
  height: 5px;
  margin-top: 6px;
}

.school-card-footer {
  display: grid;
  gap: 8px;
  margin: 0;
  border-left: 1px solid var(--benchmark-border);
  padding-left: 18px;
  color: var(--benchmark-muted);
  font-size: 10px;
}

.school-card-footer > span {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.school-card-footer small {
  color: var(--benchmark-muted);
  font-size: 9px;
}

.school-card-footer strong {
  color: var(--benchmark-ink);
  overflow-wrap: anywhere;
  font-size: 11px;
  font-weight: 650;
}

.school-history {
  grid-column: 1 / -1;
  margin: 0;
  border-radius: 6px;
  background: var(--benchmark-soft);
}

.school-history summary {
  cursor: pointer;
  padding: 9px 12px;
  color: #7f1d1d;
  font-size: 11px;
  font-weight: 700;
}

.history-list {
  display: grid;
  gap: 1px;
  border-top: 1px solid var(--benchmark-border);
}

.history-list > div {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 12px;
  padding: 8px 12px;
  color: var(--benchmark-muted);
  font-size: 10px;
  background: rgba(255, 255, 255, 0.55);
}

.history-list strong {
  color: var(--benchmark-ink);
}

.benchmark-pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-top: 22px;
}

.benchmark-pagination > div {
  display: flex;
  gap: 6px;
}

.benchmark-pagination button {
  min-width: 36px;
  min-height: 36px;
  border: 1px solid var(--benchmark-border);
  border-radius: 6px;
  padding: 0 11px;
  color: var(--benchmark-ink);
  font: inherit;
  font-size: 12px;
  background: var(--benchmark-paper);
  cursor: pointer;
}

.benchmark-pagination button:hover:not(:disabled),
.benchmark-pagination button.active {
  border-color: var(--benchmark-red);
  color: #fff;
  background: var(--benchmark-red);
}

.benchmark-pagination button:disabled {
  opacity: 0.42;
  cursor: not-allowed;
}

.benchmark-empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-top: 16px;
  border: 1px dashed var(--benchmark-border);
  border-radius: 8px;
  padding: 60px 20px;
  color: var(--benchmark-muted);
}

.benchmark-empty-state strong {
  color: var(--benchmark-ink);
}

.benchmark-empty-state span {
  margin-top: 8px;
  font-size: 12px;
}

.benchmark-methodology {
  display: grid;
  grid-template-columns: minmax(220px, 0.7fr) minmax(0, 1.3fr);
  gap: 46px;
  border-top: 1px solid var(--benchmark-border);
  border-bottom: 1px solid var(--benchmark-border);
  padding: 44px;
  color: var(--benchmark-ink);
  background: var(--benchmark-soft);
}

.benchmark-methodology > div:first-child > span {
  color: var(--benchmark-red);
}

.benchmark-methodology h2 {
  color: var(--benchmark-ink);
}

.methodology-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.methodology-grid article {
  border-left: 2px solid #dc2626;
  padding: 2px 0 2px 18px;
}

.methodology-grid article:nth-child(2) {
  border-left-color: #2563eb;
}

.methodology-grid article:nth-child(3) {
  border-left-color: #16a34a;
}

.methodology-grid strong {
  color: var(--benchmark-ink);
  font-size: 13px;
}

.methodology-grid p {
  margin: 10px 0 0;
  color: var(--benchmark-muted);
  font-size: 11px;
  line-height: 1.75;
}

@media (max-width: 960px) {
  .benchmark-hero {
    grid-template-columns: 1fr;
    padding: 48px 0;
  }

  .benchmark-kpis,
  .version-card-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .benchmark-toolbar {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .school-card {
    grid-template-columns: minmax(0, 1fr) 120px;
  }

  .school-coverage-list,
  .school-card-footer {
    grid-column: 1 / -1;
  }

  .school-card-footer {
    display: flex;
    gap: 28px;
    border-top: 1px solid var(--benchmark-border);
    border-left: 0;
    padding-top: 12px;
    padding-left: 0;
  }

  .benchmark-methodology {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .benchmark-page {
    padding: 22px 16px 56px;
  }

  .benchmark-hero {
    gap: 34px;
    padding: 38px 0 34px;
  }

  .benchmark-hero h1 {
    font-size: 38px;
  }

  .benchmark-hero-copy > p {
    font-size: 15px;
  }

  .benchmark-kpis,
  .version-card-grid,
  .methodology-grid {
    grid-template-columns: 1fr;
  }

  .benchmark-hero-visual {
    display: none;
  }

  .benchmark-section-heading h2,
  .benchmark-methodology h2 {
    font-size: 28px;
  }

  .benchmark-section-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 14px;
  }

  .benchmark-toolbar {
    grid-template-columns: 1fr;
  }

  .benchmark-result-count {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }

  .school-card {
    grid-template-columns: 1fr;
    gap: 14px;
    padding: 16px;
  }

  .candidate-highlight,
  .school-coverage-list,
  .school-card-footer,
  .school-history {
    grid-column: 1;
  }

  .candidate-highlight {
    display: grid;
    grid-template-columns: 1fr auto;
    border-top: 1px solid var(--benchmark-border);
    border-left: 0;
    padding: 12px 0 0;
  }

  .candidate-highlight > strong {
    grid-row: 1 / span 2;
    grid-column: 2;
  }

  .school-card-footer {
    flex-wrap: wrap;
    gap: 12px 24px;
  }

  .benchmark-pagination {
    gap: 7px;
  }

  .benchmark-pagination button {
    min-width: 34px;
    min-height: 34px;
    padding: 0 8px;
  }

  .benchmark-methodology {
    gap: 28px;
    padding: 30px 22px;
  }
}

</style>

<style>
html.dark .benchmark-page {
  --benchmark-ink: #f4f4f5;
  --benchmark-muted: #a1a1aa;
  --benchmark-border: rgba(255, 255, 255, 0.1);
  --benchmark-paper: #1d1f22;
  --benchmark-soft: #25272b;
}

html.dark .benchmark-hero {
  border-color: rgba(255, 255, 255, 0.08);
}

html.dark .benchmark-hero h1,
html.dark .benchmark-section-heading h2,
html.dark .school-card h3,
html.dark .benchmark-kpis strong,
html.dark .version-candidate-count,
html.dark .candidate-highlight > strong,
html.dark .hero-coverage-item strong,
html.dark .school-coverage-list strong {
  color: #fafafa;
}

html.dark .benchmark-hero-visual,
html.dark .benchmark-toolbar input {
  background: #1d1f22;
}

html.dark .benchmark-secondary-action {
  border-color: rgba(248, 113, 113, 0.35);
  color: #fecaca !important;
  background: #25272b;
}

html.dark .hero-coverage-track,
html.dark .school-progress {
  background: rgba(255, 255, 255, 0.09);
}

html.dark .history-list > div {
  background: rgba(255, 255, 255, 0.025);
}
</style>
