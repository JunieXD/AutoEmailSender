# 官网与文档站实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 README 收缩为项目入口，并新增一个独立的中文官网与文档站，普通用户从官网进入下载和文档，开发者从文档站查看本地运行与打包说明。

**架构：** 使用一个独立的 `website/` 站点承载官网和文档内容，采用静态站点生成器输出中文页面和文档路由。README 只保留简介、下载入口、官网入口、文档入口和问题反馈入口，不再放本地开发流程。站点通过 GitHub Pages 发布，下载按钮始终指向 GitHub Releases。

**技术栈：** VitePress、GitHub Pages、GitHub Actions、Markdown。

---

### 任务 1：建立网站骨架

**文件：**
- 创建：`website/package.json`
- 创建：`website/index.md`
- 创建：`website/zh/index.md`
- 创建：`website/zh/docs/index.md`
- 创建：`website/zh/docs/getting-started.md`
- 创建：`website/zh/docs/install.md`
- 创建：`website/zh/docs/developer.md`
- 创建：`website/.vitepress/config.mts`
- 创建：`website/.vitepress/theme/index.ts`

- [ ] **步骤 1：编写首页与文档页的最小内容**

```md
# Auto Email Sender

面向导师套磁场景的智能邮件助手。

- [下载 Windows 安装包](https://github.com/JunieXD/AutoEmailSender/releases)
- [查看文档](./docs/getting-started)
```

- [ ] **步骤 2：验证站点可以启动**

运行：`cd website && npm install && npm run docs:dev`
预期：站点可以打开首页和文档页，无 404。

- [ ] **步骤 3：提交首个站点骨架**

```bash
git add website
git commit -m "feat(website): add docs portal skeleton"
```

### 任务 2：精简 README 为入口

**文件：**
- 修改：`README.md`

- [ ] **步骤 1：保留项目介绍与入口链接**

```md
## 入口

- [官网](https://<pages-url>/zh/)
- [用户文档](https://<pages-url>/zh/docs/getting-started)
- [开发者文档](https://<pages-url>/zh/docs/developer)
- [下载 Windows 安装包](https://github.com/JunieXD/AutoEmailSender/releases)
- [问题反馈](https://github.com/JunieXD/AutoEmailSender/issues)
```

- [ ] **步骤 2：移除本地开发流程**

保留安装和使用导向内容，删除开发者命令、桌面调试和本地打包说明。

- [ ] **步骤 3：检查 README 语气与排版**

运行：`git diff -- README.md`
预期：只有入口类内容，没有开发流程和发布脚本说明。

### 任务 3：添加 GitHub Pages 自动部署

**文件：**
- 创建：`.github/workflows/website.yml`

- [ ] **步骤 1：增加构建与发布工作流**

```yml
name: Deploy Website

on:
  push:
    branches: [master]
    paths:
      - "website/**"
      - ".github/workflows/website.yml"

permissions:
  contents: read
  pages: write
  id-token: write

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 24
      - run: npm ci
        working-directory: website
      - run: npm run build
        working-directory: website
      - uses: actions/upload-pages-artifact@v3
        with:
          path: website/.vitepress/dist

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```

- [ ] **步骤 2：在仓库设置中启用 Pages**

确认 Pages 源为 GitHub Actions，部署目录为工作流产物。

- [ ] **步骤 3：检查 workflow 触发范围**

运行：`git diff -- .github/workflows/website.yml`
预期：只在 `website/**` 变更时部署站点。

### 任务 4：补充验证

**文件：**
- 修改：`website/package.json`（如需要）
- 修改：`README.md`

- [ ] **步骤 1：本地构建网站**

运行：`cd website && npm run build`
预期：生成静态站点，无构建错误。

- [ ] **步骤 2：检查 README 入口链接**

确认链接指向 GitHub Pages 和 Releases。

- [ ] **步骤 3：最终检查**

运行：`git diff --check`
预期：无格式问题。
