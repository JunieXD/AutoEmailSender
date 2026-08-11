---
layout: home
hero:
  name: Auto Email Sender
  text: 面向导师联系场景的智能邮件助手
  tagline: 抓导师、看匹配、写草稿、定时发送、追踪回复。重复工作交给工具，关键决定由你来做。
  image:
    src: /logo.svg
    alt: Auto Email Sender
  actions:
    - theme: brand
      text: 快速开始
      link: /docs/getting-started
    - theme: alt
      text: 去 GitHub 点 Star 🌟
      link: https://github.com/JunieXD/AutoEmailSender
    - theme: alt
      text: 加入 QQ 交流群
      link: /docs/feedback
---

<script setup>
import CrawlBenchmarkPromo from './.vitepress/theme/components/CrawlBenchmarkPromo.vue'
</script>

<section class="home-screenshot-section">
  <div class="home-screenshot-card">
    <img src="/screenshots/docs/home/app-home-overview.png" alt="Auto Email Sender 导师看板" />
  </div>
</section>

<CrawlBenchmarkPromo />

<section class="home-feature-section">
  <div class="home-feature-heading">
    <h2>从找导师到发送邮件，一站完成</h2>
    <p>系统帮你整理导师、生成草稿；你决定联系谁、写什么、何时发送。</p>
  </div>

  <div class="home-feature-grid">
    <article>
      <span>01</span>
      <h3>智能抓取</h3>
      <p>从学校官网整理导师信息，自动补全邮箱、院系、研究方向和主页链接。</p>
    </article>
    <article>
      <span>02</span>
      <h3>匹配度分析</h3>
      <p>结合你的材料和导师近期研究，给出匹配理由和联系建议。</p>
    </article>
    <article>
      <span>03</span>
      <h3>定时批量发送</h3>
      <p>审核草稿后，可立即发送或按日期、时段和数量定时发送。</p>
    </article>
    <article>
      <span>04</span>
      <h3>回复追踪</h3>
      <p>自动检测回复并更新任务状态，联系进度一目了然。</p>
    </article>
    <article>
      <span>05</span>
      <h3>社区导师库</h3>
      <p>直接浏览和导入已整理的公开导师资料，导入前可预览差异。</p>
    </article>
    <article>
      <span>06</span>
      <h3>可控的自动化</h3>
      <p>任务中心集中审核草稿和发送计划，也可按需接入本地 Agent。</p>
    </article>
  </div>
</section>
