import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const profilePageSource = readFileSync(
  resolve(process.cwd(), "src/pages/ProfilePage.tsx"),
  "utf8",
);

const getSectionSource = (sectionId: string, nextSectionId?: string) => {
  const start = profilePageSource.indexOf(`sectionId="${sectionId}"`);
  expect(start).toBeGreaterThanOrEqual(0);

  if (!nextSectionId) {
    return profilePageSource.slice(start);
  }

  const end = profilePageSource.indexOf(`sectionId="${nextSectionId}"`, start);
  expect(end).toBeGreaterThan(start);
  return profilePageSource.slice(start, end);
};

describe("ProfilePage setup sections", () => {
  it("keeps identity save actions in the sender identity section", () => {
    const identitySection = getSectionSource("identity", "materials");
    const testSection = getSectionSource("test");

    expect(identitySection).toContain("{identityActionButtons}");
    expect(testSection).not.toContain("保存配置");
    expect(testSection).not.toContain("{identityActionButtons}");
  });

  it("saves identity changes when completing template editing", () => {
    const modalSource = profilePageSource.slice(
      profilePageSource.indexOf("const OutreachTemplateModal = ({"),
      profilePageSource.indexOf("const MaterialLibraryModal = ({"),
    );

    expect(modalSource).toContain("savingTemplate");
    expect(modalSource).toContain("onComplete");
    expect(modalSource).toContain("onClick={onComplete}");
    expect(modalSource).not.toContain("保存身份后生效。");

    const modalUsageSource = profilePageSource.slice(
      profilePageSource.indexOf("<OutreachTemplateModal"),
    );
    expect(modalUsageSource).toContain("savingTemplate={submittingIdentity}");
    expect(modalUsageSource).toContain("onComplete={() =>");
    expect(modalUsageSource).toContain(
      "saveIdentity({ validateTemplate: true }).then((saved) =>",
    );
  });

  it("supports drag-and-drop template import in the default template modal", () => {
    const modalSource = profilePageSource.slice(
      profilePageSource.indexOf("const OutreachTemplateModal = ({"),
      profilePageSource.indexOf("const MaterialLibraryModal = ({"),
    );

    expect(modalSource).toContain("isTemplateDropActive");
    expect(modalSource).toContain("onDragOver={handleTemplateDragOver}");
    expect(modalSource).toContain("onDrop={handleTemplateDrop}");
  });

  it("shows a docx drag hint when the default template body is empty", () => {
    const modalSource = profilePageSource.slice(
      profilePageSource.indexOf("const OutreachTemplateModal = ({"),
      profilePageSource.indexOf("const MaterialLibraryModal = ({"),
    );

    expect(modalSource).toContain('placeholder="可将套磁信docx拖到此处导入"');
  });

  it("treats default template body completion as visible text instead of residual html", () => {
    const summarySource = profilePageSource.slice(
      profilePageSource.indexOf("const OutreachTemplateSummaryCard = ({"),
      profilePageSource.indexOf("const OutreachTemplateModal = ({"),
    );
    const modalSource = profilePageSource.slice(
      profilePageSource.indexOf("const OutreachTemplateModal = ({"),
      profilePageSource.indexOf("const MaterialLibraryModal = ({"),
    );
    const importSource = profilePageSource.slice(
      profilePageSource.indexOf("const handleTemplateFileImport = async"),
      profilePageSource.indexOf("const runLlmConnectionTest = async"),
    );

    expect(profilePageSource).toContain("const hasVisibleTemplateBody =");
    expect(summarySource).toContain("hasVisibleTemplateBody(form)");
    expect(modalSource).toContain("hasVisibleTemplateBody(form)");
    expect(importSource).toContain("hasVisibleTemplateBody(identityForm)");
    expect(summarySource).not.toContain("outreach_template_body_html.trim()");
    expect(modalSource).not.toContain("outreach_template_body_html.trim()");
  });

  it("uses the draft llm payload for preview actions", () => {
    expect(profilePageSource).toContain("fetchLLMProfileModelsPreview");
    expect(profilePageSource).toContain("testLLMProfilePreview");
    expect(profilePageSource).toContain("toLLMPayload(llmForm)");
  });
  it("opens materials through desktop api and keeps download endpoint", () => {
    const materialModalSource = profilePageSource.slice(
      profilePageSource.indexOf("const MaterialLibraryModal = ({"),
    );

    expect(profilePageSource).toContain("openDesktopMaterial(material.id)");
    expect(materialModalSource).toContain("onClick={() => onOpen(material)}");
    expect(materialModalSource).toContain("triggerDownload(getMaterialDownloadUrl(material.id))");
    expect(materialModalSource).not.toContain("getMaterialOpenUrl(material.id)");
    expect(materialModalSource).not.toContain("openFileInNewTab(getMaterialOpenUrl");
    expect(profilePageSource).not.toContain('link.target = "_blank"');
    expect(profilePageSource).not.toContain('link.rel = "noreferrer"');
  });

  it("does not render the legacy token usage center in profile settings", () => {
    expect(profilePageSource).not.toContain("TokenUsageCenterCard");
    expect(profilePageSource).not.toContain("Token 消耗记录中心");
  });
});
