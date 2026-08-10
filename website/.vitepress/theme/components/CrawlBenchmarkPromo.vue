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
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(380px, 0.9fr);
  gap: 48px;
  overflow: hidden;
  margin: 72px auto 0;
  max-width: 1120px;
  border-top: 3px solid #dc2626;
  border-radius: 8px;
  padding: 46px 50px;
  color: #fff;
  background: #18181b;
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.14);
}

.home-benchmark-copy > span {
  color: #fecaca;
  font-size: 12px;
  font-weight: 750;
  letter-spacing: 0;
}

.home-benchmark-copy h2 {
  margin: 12px 0 0;
  color: #fff;
  font-size: 30px;
  line-height: 1.25;
}

.home-benchmark-copy p {
  margin: 14px 0 0;
  color: rgba(255, 255, 255, 0.72);
  font-size: 14px;
  line-height: 1.75;
}

.home-benchmark-copy a {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  margin-top: 22px;
  border-radius: 999px;
  padding: 11px 17px;
  color: #7f1d1d;
  font-size: 13px;
  font-weight: 750;
  text-decoration: none;
  background: #fff;
}

.home-benchmark-copy a i {
  font-style: normal;
  transition: transform 160ms ease;
}

.home-benchmark-copy a:hover i {
  transform: translateX(3px);
}

.home-benchmark-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  align-self: center;
}

.home-benchmark-metrics > div {
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 6px;
  padding: 18px;
  background: rgba(255, 255, 255, 0.07);
  backdrop-filter: blur(10px);
}

.home-benchmark-metrics strong,
.home-benchmark-metrics span {
  display: block;
}

.home-benchmark-metrics strong {
  font-size: 28px;
}

.home-benchmark-metrics span {
  margin-top: 4px;
  color: rgba(255, 255, 255, 0.65);
  font-size: 10px;
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
