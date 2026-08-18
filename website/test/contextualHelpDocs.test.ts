import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const readWebsiteFile = (path: string) => readFileSync(resolve(path), "utf8");

const faq = readWebsiteFile("docs/faq.md");
const firstRun = readWebsiteFile("docs/first-run.md");
const gettingStarted = readWebsiteFile("docs/getting-started.md");
const profile = readWebsiteFile("docs/profile.md");

describe("contextual help documentation", () => {
  it("keeps stable anchors for links shipped inside the desktop app", () => {
    expect(firstRun).toContain(
      "### 邮箱授权码教程 {#mail-authorization-code}",
    );
    expect(firstRun).toContain("## 2. 配置模型 {#llm-configuration}");
  });

  it("uses the stable anchors in related documentation", () => {
    const relatedDocs = [faq, gettingStarted, profile].join("\n");
    expect(relatedDocs).toContain("./first-run#mail-authorization-code");
    expect(relatedDocs).toContain("./first-run#llm-configuration");
    expect(relatedDocs).not.toContain("#邮箱授权码教程");
    expect(relatedDocs).not.toContain("#_2-配置模型");
  });

  it("explains how to create and protect a DeepSeek API Key", () => {
    expect(firstRun).toContain("https://platform.deepseek.com/api_keys");
    expect(firstRun).toContain("点击“创建 API key”");
    expect(firstRun).toContain("立即复制生成的密钥");
    expect(firstRun).toContain("请不要发送给他人，也不要在截图中公开");
  });
});
