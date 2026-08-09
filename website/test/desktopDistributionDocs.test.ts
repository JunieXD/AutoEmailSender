import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const readWebsiteFile = (path: string) => readFileSync(resolve(path), "utf8");

const config = readWebsiteFile(".vitepress/config.mts");
const developer = readWebsiteFile("docs/developer.md");
const faq = readWebsiteFile("docs/faq.md");
const gettingStarted = readWebsiteFile("docs/getting-started.md");
const docsIndex = readWebsiteFile("docs/index.md");
const install = readWebsiteFile("docs/install.md");

describe("desktop distribution documentation", () => {
  it("uses cross-platform installation labels and current artifact names", () => {
    expect(config).toContain('text: "安装桌面版"');
    expect(docsIndex).toContain("[安装桌面版](./install)");
    expect(developer).toContain("普通用户请直接[安装桌面版](./install)");
    expect(install).toContain("`AutoEmailSender-Setup-x.y.z.exe`");
    expect(gettingStarted).toContain("详细步骤和安全提示见[安装桌面版](./install)");

    const currentDocs = [config, developer, docsIndex, gettingStarted, install].join("\n");
    expect(currentDocs).not.toContain("安装 Windows 版");
    expect(currentDocs).not.toContain("AutoEmailSender Setup x.y.z.exe");
  });

  it("documents the userData directories used by the packaged app", () => {
    expect(developer).toContain("%APPDATA%\\auto-email-sender-desktop");
    for (const document of [faq, install]) {
      expect(document).toContain("AppData\\Roaming\\auto-email-sender-desktop");
    }
    for (const document of [developer, faq, install]) {
      expect(document).toContain("~/Library/Application Support/auto-email-sender-desktop");
      expect(document).not.toContain("Application Support/Auto Email Sender");
      expect(document).not.toContain("AppData\\Roaming\\Auto Email Sender");
    }
  });

  it("describes the current macOS signing model precisely", () => {
    for (const document of [faq, install]) {
      expect(document).toContain("ad-hoc 签名");
      expect(document).toContain("未使用 Developer ID 签名和 Apple 公证");
    }
  });
});
