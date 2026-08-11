<script setup lang="ts">
import { withBase } from "vitepress";
import {
  buildBenchmarkSummary,
  buildLatestBenchmarkRecords,
  crawlBenchmarkPayload,
  formatBenchmarkNumber,
  formatCoverage,
} from "../crawlBenchmark";

const summary = buildBenchmarkSummary(buildLatestBenchmarkRecords(crawlBenchmarkPayload.records));
</script>

<template>
  <section class="home-benchmark-promo">
    <div class="home-benchmark-copy">
      <span>真实高校官网实测</span>
      <h2>真实官网抓取效果</h2>
      <p>查看各学院的候选数量、字段覆盖率、耗时和版本变化。</p>
      <a :href="withBase('/crawl-benchmark')">查看实测数据 <i>→</i></a>
    </div>
    <div class="home-benchmark-metrics">
      <div><strong>{{ summary.universityCount }}</strong><span>所高校</span></div>
      <div><strong>{{ summary.targetCount }}</strong><span>个学院/机构</span></div>
      <div><strong>{{ formatBenchmarkNumber(summary.candidateCount) }}</strong><span>位候选导师</span></div>
      <div><strong>{{ formatCoverage(summary.emailCoverage) }}</strong><span>邮箱字段覆盖</span></div>
    </div>
  </section>
</template>

<style scoped>
.home-benchmark-promo {
  --promo-accent: #b91c1c;
  --promo-accent-hover: #991b1b;
  --promo-button: #b91c1c;
  --promo-button-hover: #991b1b;
  --promo-ink: #29201e;
  --promo-muted: #75615a;
  --promo-border: rgba(127, 29, 29, 0.14);
  --promo-metric-bg: rgba(255, 255, 255, 0.78);
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(380px, 0.9fr);
  gap: 48px;
  overflow: hidden;
  margin: 72px auto 0;
  max-width: 1120px;
  border: 1px solid var(--promo-border);
  border-top: 3px solid var(--promo-accent);
  border-radius: 18px;
  padding: 46px 50px;
  color: var(--promo-ink);
  background:
    radial-gradient(circle at 8% 8%, rgba(185, 28, 28, 0.09), transparent 34%),
    radial-gradient(circle at 92% 88%, rgba(180, 83, 9, 0.06), transparent 32%),
    #fffaf5;
  box-shadow:
    0 24px 56px rgba(127, 29, 29, 0.1),
    0 1px 0 rgba(255, 255, 255, 0.9) inset;
}

.home-benchmark-copy > span {
  color: var(--promo-accent);
  font-size: 12px;
  font-weight: 750;
  letter-spacing: 0.04em;
}

.home-benchmark-copy h2 {
  margin: 12px 0 0;
  color: var(--promo-ink);
  font-size: 30px;
  line-height: 1.25;
}

.home-benchmark-copy p {
  margin: 14px 0 0;
  color: var(--promo-muted);
  font-size: 14px;
  line-height: 1.75;
}

.home-benchmark-copy a {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  margin-top: 22px;
  border-radius: 10px;
  padding: 11px 17px;
  color: #fff;
  font-size: 13px;
  font-weight: 750;
  text-decoration: none;
  background: var(--promo-button);
  box-shadow: 0 10px 24px rgba(127, 29, 29, 0.18);
  transition:
    background-color 180ms ease,
    box-shadow 180ms ease,
    transform 180ms ease;
}

.home-benchmark-copy a i {
  font-style: normal;
  transition: transform 160ms ease;
}

.home-benchmark-copy a:hover i {
  transform: translateX(3px);
}

.home-benchmark-copy a:hover {
  color: #fff;
  background: var(--promo-button-hover);
  box-shadow: 0 12px 28px rgba(127, 29, 29, 0.24);
  transform: translateY(-1px);
}

.home-benchmark-copy a:active {
  transform: translateY(0);
}

.home-benchmark-copy a:focus-visible {
  outline: 3px solid rgba(185, 28, 28, 0.2);
  outline-offset: 3px;
}

.home-benchmark-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  align-self: center;
}

.home-benchmark-metrics > div {
  border: 1px solid var(--promo-border);
  border-radius: 12px;
  padding: 18px;
  background: var(--promo-metric-bg);
  backdrop-filter: blur(10px);
  box-shadow:
    0 10px 24px rgba(127, 29, 29, 0.05),
    0 1px 0 rgba(255, 255, 255, 0.9) inset;
}

.home-benchmark-metrics strong,
.home-benchmark-metrics span {
  display: block;
}

.home-benchmark-metrics strong {
  color: var(--promo-accent-hover);
  font-size: 28px;
  font-variant-numeric: tabular-nums;
}

.home-benchmark-metrics span {
  margin-top: 4px;
  color: var(--promo-muted);
  font-size: 10px;
}

:global(html.dark .home-benchmark-promo) {
  --promo-accent: #f87171;
  --promo-accent-hover: #ef4444;
  --promo-button: #b91c1c;
  --promo-button-hover: #dc2626;
  --promo-ink: #fff8f5;
  --promo-muted: #d7c4bd;
  --promo-border: rgba(248, 113, 113, 0.2);
  --promo-metric-bg: rgba(255, 255, 255, 0.06);
  background:
    radial-gradient(circle at 8% 8%, rgba(185, 28, 28, 0.2), transparent 36%),
    radial-gradient(circle at 92% 88%, rgba(180, 83, 9, 0.1), transparent 32%),
    #281e1c;
  box-shadow: 0 24px 56px rgba(0, 0, 0, 0.24);
}

:global(html.dark .home-benchmark-metrics > div) {
  box-shadow:
    0 10px 24px rgba(0, 0, 0, 0.14),
    0 1px 0 rgba(255, 255, 255, 0.08) inset;
}

@media (max-width: 900px) {
  .home-benchmark-promo {
    grid-template-columns: 1fr;
    margin-right: 24px;
    margin-left: 24px;
  }
}

@media (max-width: 640px) {
  .home-benchmark-promo {
    gap: 30px;
    margin: 48px 16px 0;
    border-radius: 8px;
    padding: 32px 24px;
  }

  .home-benchmark-metrics {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
