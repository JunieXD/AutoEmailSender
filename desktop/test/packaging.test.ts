import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const readIconEntries = (iconPath: string) => {
  const icon = readFileSync(iconPath);
  const count = icon.readUInt16LE(4);

  return Array.from({ length: count }, (_, index) => {
    const offset = 6 + index * 16;
    const width = icon[offset] === 0 ? 256 : icon[offset];
    const height = icon[offset + 1] === 0 ? 256 : icon[offset + 1];
    const bytes = icon.readUInt32LE(offset + 8);
    const imageOffset = icon.readUInt32LE(offset + 12);
    const image = icon.subarray(imageOffset, imageOffset + bytes);

    return { width, height, image };
  });
};

describe("windows installer packaging", () => {
  it("builds preload as CommonJS for Electron sandbox preload", () => {
    const packageJson = readFileSync(path.resolve("package.json"), "utf8");

    expect(packageJson).toContain("tsconfig.preload.json");
  });

  it("uses the project icon for Windows builds", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");

    expect(config).toContain("icon: build/icon.ico");
    expect(config).toContain("installerIcon: build/icon.ico");
    expect(config).toContain("uninstallerIcon: build/icon.ico");
    expect(existsSync(path.resolve("build", "icon.ico"))).toBe(true);
  });

  it("packages the window icon as a runtime resource", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");

    expect(config).toContain("from: build/icon.ico");
    expect(config).toContain("to: build/icon.ico");
    expect(config).toContain("from: build/icon.png");
    expect(config).toContain("to: build/icon.png");
    expect(existsSync(path.resolve("build", "icon.png"))).toBe(true);
  });

  it("packages Playwright browsers as runtime resources", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");

    expect(config).toContain("from: ../backend/ms-playwright");
    expect(config).toContain("to: ms-playwright");
  });

  it("uses a multi-size PNG-backed Windows icon", () => {
    const entries = readIconEntries(path.resolve("build", "icon.ico"));

    expect(entries.map(({ width }) => width)).toEqual([16, 24, 32, 48, 64, 128, 256]);
    expect(entries.map(({ height }) => height)).toEqual([16, 24, 32, 48, 64, 128, 256]);
    for (const entry of entries) {
      expect(entry.image.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
    }
  });

  it("uses an assisted installer with selectable install directory", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");

    expect(config).toContain("oneClick: false");
    expect(config).toContain("allowToChangeInstallationDirectory: true");
    expect(config).toContain("createDesktopShortcut: true");
  });

  it("keeps app data cleanup as an opt-in uninstall section", () => {
    const scriptPath = path.resolve("build", "installer.nsh");
    const script = readFileSync(scriptPath, "utf8");

    expect(existsSync(scriptPath)).toBe(true);
    expect(script).toContain('Section /o "un.删除本地数据（数据库、材料、缓存和本地配置）"');
    expect(script).not.toContain("是否同时删除本地数据");
    expect(script).not.toContain('Section "un.DeleteAutoEmailSenderAppData"');
    expect(script).toContain("--delete-app-data");
    expect(script).toContain("永久删除 Auto Email Sender 的本地数据");
    expect(script).toContain("MessageBox MB_ICONEXCLAMATION|MB_YESNO|MB_DEFBUTTON2");
    expect(script).toContain("$APPDATA\\Auto Email Sender");
    expect(script).toContain("!macro customUnInstallSection");
    expect(script).toContain("un.ConfirmAndDeleteAutoEmailSenderAppData");
    expect(script).toContain("un.DeleteAutoEmailSenderAppDataFromFlag");
  });
});

describe("macOS desktop packaging", () => {
  it("builds an ad-hoc signed dmg with a macOS icon", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");

    expect(config).toContain("mac:");
    expect(config).toContain("target: dmg");
    expect(config).toContain("icon: build/icon.icns");
    expect(config).toContain('identity: "-"');
    expect(config).not.toContain("identity: null");
    expect(existsSync(path.resolve("build", "icon.icns"))).toBe(true);
  });

  it("embeds Sparkle with a signed feed and user-confirmed installation", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");

    expect(config).toContain("SUFeedURL:");
    expect(config).toContain("afterPack: ../scripts/configure-sparkle-info.mjs");
    expect(config).toContain("SUEnableAutomaticChecks: true");
    expect(config).toContain("SUAllowsAutomaticUpdates: false");
    expect(config).toContain("SURequireSignedFeed: true");
    expect(config).toContain("from: native/sparkle/build/Release/sparkle_bridge.node");
    expect(config).toContain("from: native/sparkle/vendor/Sparkle.framework");
    expect(config).toContain("to: Frameworks/Sparkle.framework");
    const hook = readFileSync(path.resolve("..", "scripts", "configure-sparkle-info.mjs"), "utf8");
    expect(hook).toContain("process.env.SPARKLE_PUBLIC_ED_KEY");
    expect(hook).toContain('decoded.length !== 32');
    expect(hook).toContain('"-insert", "SUPublicEDKey"');
    const setupScript = readFileSync(path.resolve("..", "scripts", "setup-sparkle.sh"), "utf8");
    expect(setupScript).toContain('sparkle_version="2.9.4"');
    expect(setupScript).toContain("ce89daf967db1e1893ed3ebd67575ed82d3902563e3191ca92aaec9164fbdef9");
  });

  it("keeps platform-specific artifact names", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");

    expect(config).toContain('artifactName: "AutoEmailSender-Setup-${version}.${ext}"');
    expect(config).toContain('artifactName: "AutoEmailSender-${version}-${arch}.${ext}"');
  });

  it("declares macOS package scripts without changing Windows scripts", () => {
    const packageJson = readFileSync(path.resolve("package.json"), "utf8");

    expect(packageJson).toContain('"pack": "npm run build && electron-builder --config electron-builder.yml --win --dir"');
    expect(packageJson).toContain('"dist": "npm run build && electron-builder --config electron-builder.yml --win nsis --publish never"');
    expect(packageJson).toContain('"publish": "npm run build && electron-builder --config electron-builder.yml --win nsis --publish always"');
    expect(packageJson).toContain('"pack:mac": "npm run prepare:sparkle && npm run build && electron-builder --config electron-builder.yml --mac --dir --publish never"');
    expect(packageJson).toContain('"dist:mac": "npm run prepare:sparkle && npm run build && electron-builder --config electron-builder.yml --mac dmg --publish never"');
    expect(packageJson).not.toContain('"publish:mac"');
  });

  it("publishes both platforms together and uploads appcast last", () => {
    const workflow = readFileSync(path.resolve("..", ".github", "workflows", "release.yml"), "utf8");

    expect(workflow).toContain("build-windows:");
    expect(workflow).toContain("build-macos:");
    expect(workflow).toContain("publish:");
    expect(workflow).toContain("needs:");
    expect(workflow).toContain("scripts/prepare-sparkle-release.mjs");
    expect(workflow.indexOf("release-assets/macos/appcast.xml --clobber")).toBeGreaterThan(
      workflow.indexOf('gh release upload "${{ github.ref_name }}" "${assets[@]}" --clobber'),
    );
  });
});
