#!/usr/bin/env node

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { parsePrereleaseVersion } from "./prerelease-contract.mjs";

export function buildPrereleaseNotes({ version, channel }) {
  const parsed = parsePrereleaseVersion(version, channel);
  const tag = `v${parsed.value}`;
  const label = parsed.channel === "alpha"
    ? "Alpha"
    : parsed.channel === "beta"
      ? "Beta"
      : "RC";
  return `# ${tag}（${label} 测试版）

> [!WARNING]
> 这是用于主动测试的非稳定版本，可能出现后台任务中断、资源占用升高或需要切回兼容模式等问题。请勿在没有备份的日常数据上直接试用。

## 测试版说明

- 本版本不会通过应用内“检查更新”提供，也不会成为 GitHub Latest；只面向从 GitHub Prerelease 手动下载安装的测试者。
- 测试版本地诊断只保存在电脑上，不会自动上传；只有主动导出 ZIP 并自行发送时才会离开本机。

### 新增功能

- 待根据本次候选的用户可见变化补充。

### 体验优化

- 待根据本次候选的用户可见变化补充。

### 问题修复

- 待根据本次候选的用户可见变化补充。

## 测试重点

- 待列出本次需要重点覆盖的正常流程、模式切换和故障场景。
- 健康运行也请定期导出一份诊断包，避免样本只包含故障设备。

## 安装前备份

- 完全退出 Auto Email Sender，并备份应用数据目录；如本次包含数据库迁移，请同时确认升级前自动备份已经生成。
- 测试中不要配置真实导师、日常邮箱或生产密钥；优先使用隔离数据和 loopback fake 服务。

## 安装与覆盖升级

- Windows：从本 Prerelease 下载 \`AutoEmailSender-Setup-${parsed.value}.exe\`，在保留现有数据的情况下覆盖安装。
- macOS Apple Silicon：从本 Prerelease 下载 \`AutoEmailSender-${parsed.value}-arm64.dmg\` 并覆盖安装；Intel Mac 暂不支持。
- macOS 若首次打开被阻止，请前往“系统设置 > 隐私与安全性”选择“仍要打开”。

## 回退与诊断

- 如 API + Worker 测试模式不稳定，请在“其他设置”切换到“单进程兼容模式”并重启；这只切换同一版本的运行拓扑，不会降级数据库。
- 页面无法打开时，可从托盘或原生启动失败窗口导出 partial 诊断包。诊断包不包含数据库副本、邮件正文、附件、导师资料或凭据。
- SMTP 发送结果不确定时不会自动重发，也不要求用户确认或依赖 Sent/IMAP 证据。

## 自动更新隔离

- 稳定版客户端不会检测到本测试版；本测试版也不发布稳定通道使用的 \`latest.yml\`、\`appcast.xml\` 或差分包。
- 后续测试版使用新的、更高 prerelease 版本，不覆盖已经公开的安装包。

## 停止使用与取代

- 如果本版本被标记为“停止使用”，请停止继续测试并按公告导出最后一份诊断包。
- 修复版本会以更高的 ${label} / RC 版本另行发布；旧资产保留用于审计，不会被替换。
`;
}

function parseArguments(argv) {
  const options = { version: "", channel: "", output: "" };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    const value = argv[index + 1];
    if (!argument.startsWith("--") || value === undefined) {
      throw new Error(`无法解析参数：${argument}`);
    }
    index += 1;
    if (argument === "--version") options.version = value;
    else if (argument === "--channel") options.channel = value;
    else if (argument === "--output") options.output = path.resolve(value);
    else throw new Error(`未知参数：${argument}`);
  }
  if (!options.version || !options.channel || !options.output) {
    throw new Error("用法: prerelease-notes.mjs --version <x.y.z-beta.n> --channel <channel> --output <path>");
  }
  return options;
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  await mkdir(path.dirname(options.output), { recursive: true });
  await writeFile(options.output, buildPrereleaseNotes(options), { encoding: "utf8", flag: "wx" });
  console.log(`[ok] 已生成 ${options.output}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
