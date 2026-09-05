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
      "{#mail-authorization-code}",
    );
    expect(firstRun).toContain("{#llm-configuration}");
  });

  it("uses the stable anchors in related documentation", () => {
    const relatedDocs = [faq, gettingStarted, profile].join("\n");
    expect(relatedDocs).toContain("./first-run#mail-authorization-code");
    expect(relatedDocs).toContain("./first-run#llm-configuration");
    expect(relatedDocs).not.toContain("#邮箱授权码教程");
    expect(relatedDocs).not.toContain("#_2-配置模型");
  });

});
