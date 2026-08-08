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

describe("desktop development", () => {
  it("prepares the Agent CLI before launching Electron", () => {
    const packageJson = JSON.parse(readFileSync(path.resolve("package.json"), "utf8")) as {
      scripts: { dev: string };
    };

    expect(packageJson.scripts.dev).toBe(
      "npm run build && node dist/src/main/agent-support/prepare-dev-cli.js && node dist/src/main/dev/launcher.js",
    );
  });

  it("separates production, preload, and test TypeScript builds", () => {
    const packageJson = JSON.parse(readFileSync(path.resolve("package.json"), "utf8")) as {
      scripts: { build: string; "build:preload": string; clean: string; typecheck: string };
    };
    const productionConfig = JSON.parse(
      readFileSync(path.resolve("tsconfig.json"), "utf8"),
    ) as { include: string[] };
    const preloadConfig = JSON.parse(
      readFileSync(path.resolve("tsconfig.preload.json"), "utf8"),
    ) as { compilerOptions: { module: string; noEmit: boolean }; include: string[] };
    const testConfig = JSON.parse(
      readFileSync(path.resolve("tsconfig.test.json"), "utf8"),
    ) as { compilerOptions: { noEmit: boolean }; include: string[] };
    const vitestConfig = readFileSync(path.resolve("vitest.config.ts"), "utf8");

    expect(packageJson.scripts.build).toContain("npm run clean");
    expect(packageJson.scripts.build).toContain("npm run build:preload");
    expect(packageJson.scripts.clean).toContain("rmSync('dist'");
    expect(packageJson.scripts.typecheck).toContain("tsconfig.test.json");
    expect(productionConfig.include).toEqual(["src/**/*.ts"]);
    expect(preloadConfig.compilerOptions).toMatchObject({
      module: "CommonJS",
      noEmit: true,
    });
    expect(preloadConfig.include).toEqual([
      "src/preload.ts",
      "src/preload/**/*.ts",
      "src/contracts/**/*.ts",
    ]);
    expect(testConfig.compilerOptions.noEmit).toBe(true);
    expect(testConfig.include).toContain("test/**/*.ts");
    expect(vitestConfig).toContain('"**/dist/**"');
    expect(vitestConfig).toContain('"**/release/**"');
  });
});

describe("windows installer packaging", () => {
  it("bundles the sandbox preload as standalone CommonJS", () => {
    const packageJson = JSON.parse(readFileSync(path.resolve("package.json"), "utf8")) as {
      scripts: { "build:preload": string };
    };
    const applicationSource = readFileSync(
      path.resolve("src", "main", "bootstrap", "application.ts"),
      "utf8",
    );
    const preloadBuild = packageJson.scripts["build:preload"];

    expect(preloadBuild).toContain("tsconfig.preload.json");
    expect(preloadBuild).toContain("--bundle");
    expect(preloadBuild).toContain("--format=cjs");
    expect(preloadBuild).toContain("--external:electron");
    expect(preloadBuild).toContain("--outfile=dist/src/preload.js");
    expect(applicationSource).toContain(
      'preload: path.join(app.getAppPath(), "dist", "src", "preload.js")',
    );
    expect(applicationSource).toContain("sandbox: true");
  });

  it("uses the project icon for Windows builds", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");

    expect(config).toContain("icon: build/icon.ico");
    expect(config).toContain("installerIcon: build/icon.ico");
    expect(config).toContain("uninstallerIcon: build/icon.ico");
    expect(existsSync(path.resolve("build", "icon.ico"))).toBe(true);
  });

  it("keeps only supported Electron locale packs with Windows names", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");
    const windowsConfig = config.slice(config.indexOf("win:"), config.indexOf("mac:"));

    expect(windowsConfig).toContain(
      "  electronLanguages:\n    - en-US\n    - en-GB\n    - zh-CN\n    - zh-TW",
    );
    expect(windowsConfig).not.toContain("- zh_CN");
    expect(windowsConfig).not.toContain("- zh_TW");
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

  it("packages the platform CLI and Agent usage guide as runtime resources", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");
    const workflow = readFileSync(path.resolve("..", ".github", "workflows", "release.yml"), "utf8");

    expect(config).toContain("from: ../cli/dist/auto-email-sender");
    expect(config).toContain("to: cli");
    expect(config).not.toContain("filter:\n      - auto-email-sender");
    expect(config).toContain("from: ../agent-support");
    expect(config).toContain("to: agent-support");
    expect(workflow).toContain("./scripts/build/build-cli.ps1 -Clean");
    expect(workflow).toContain("./scripts/build/build-cli.sh --clean");
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

  it("ships and installs the signed Microsoft VC++ x64 runtime prerequisite", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");
    const packageJson = JSON.parse(readFileSync(path.resolve("package.json"), "utf8")) as {
      scripts: Record<string, string>;
    };
    const installerScript = readFileSync(path.resolve("build", "installer.nsh"), "utf8");
    const prepareScript = readFileSync(
      path.resolve("..", "scripts", "build", "prepare-windows-vc-runtime.ps1"),
      "utf8",
    );
    const workflow = readFileSync(
      path.resolve("..", ".github", "workflows", "release.yml"),
      "utf8",
    );

    expect(packageJson.scripts["prepare:windows-runtime"]).toContain(
      "prepare-windows-vc-runtime.ps1",
    );
    expect(packageJson.scripts.dist).toContain("npm run prepare:windows-runtime");
    expect(packageJson.scripts.publish).toContain("npm run prepare:windows-runtime");
    expect(config).toContain("from: build/runtime/vc_redist.x64.exe");
    expect(config).toContain("to: runtime/vc_redist.x64.exe");
    expect(prepareScript).toContain("Join-Path $PSHOME");
    expect(prepareScript).toContain("Import-Module -Name $SecurityModulePath");
    expect(prepareScript).toContain("Get-AuthenticodeSignature");
    expect(prepareScript).toContain("Microsoft Corporation");
    expect(workflow.indexOf("Prepare Windows packaging prerequisites")).toBeLessThan(
      workflow.indexOf("Install frontend dependencies"),
    );
    expect(workflow).toContain("preflight:");
    expect(workflow).toContain("node scripts/release/release-preflight.mjs");
    expect(workflow).toMatch(/build-windows:[\s\S]*?runs-on: windows-latest\n    needs: preflight/);
    expect(workflow).toMatch(/build-macos:[\s\S]*?runs-on: macos-latest\n    needs: preflight/);
    expect(workflow).toContain("working-directory: desktop\n        run: npm run dist:prepared");
    expect(workflow).toContain("build-cli.ps1 -Clean -SkipSync");
    expect(workflow).toContain("build-backend.ps1 -Clean -SkipSync");
    expect(installerScript).toContain("!macro customInstall");
    expect(installerScript).toContain("vc_redist.x64.exe\" /install /quiet /norestart");
    expect(installerScript).toContain('$R0 == "3010"');
    expect(installerScript).toContain("Abort");
  });

  it("separates quick Windows QA from uncached release integration checks", () => {
    const hostRunner = readFileSync(
      path.resolve("..", "scripts", "quality", "run-windows-vm-release-qa.sh"),
      "utf8",
    );
    const guestRunner = readFileSync(
      path.resolve("..", "scripts", "quality", "run-windows-release-qa.ps1"),
      "utf8",
    );
    const packageJson = JSON.parse(
      readFileSync(path.resolve("package.json"), "utf8"),
    ) as { scripts: Record<string, string> };

    expect(hostRunner).toContain("--force-full");
    expect(hostRunner).toContain("--quick");
    expect(hostRunner).not.toContain("--skip-runtime-lifecycle");
    expect(hostRunner).toContain("skipping Git bundle transfer");
    expect(hostRunner).toContain("Creating incremental Git bundle");
    expect(hostRunner).toContain('-PreviousRevision "$guest_revision"');
    expect(guestRunner).toContain("[switch]$ForceFull");
    expect(guestRunner).toContain('[ValidateSet("release", "quick")]');
    expect(guestRunner).toContain('[string]$Mode = "release"');
    expect(guestRunner).toContain("[string]$ExpectedRevision");
    expect(guestRunner).toContain("[string]$PreviousRevision");
    expect(guestRunner).toContain("Get-StageFingerprint");
    expect(guestRunner).toContain("Test-VerifiedStage");
    expect(guestRunner).toContain("Import-LegacyVerifiedStage");
    expect(guestRunner).toContain("toolchainFingerprint");
    expect(guestRunner).toContain("Stop-StaleQaCheckoutProcesses");
    expect(guestRunner).toContain('Test-VerifiedStage -Name "backend-suite"');
    expect(guestRunner).toContain(
      'Test-VerifiedStage -Name "backend-release-contracts"',
    );
    expect(guestRunner).toContain(
      'Test-VerifiedStage -Name "release-orchestration-contracts"',
    );
    expect(guestRunner).toContain("prepare-release.test.ps1");
    expect(guestRunner).toContain("release-script.test.ps1");
    expect(guestRunner).toContain("test.test_backend_build_script");
    expect(guestRunner).toContain('Invoke-QaStep "Windows installer build"');
    expect(guestRunner).toContain("if ($Mode -eq \"release\")");
    expect(guestRunner).toContain("npm run dist:prepared");
    expect(guestRunner).toContain(
      'Invoke-QaStep "Packaged runtime identity and stale-process lifecycle"',
    );
    expect(guestRunner).toContain("it is not valid release preflight evidence");
    expect(guestRunner).not.toContain('Test-VerifiedStage -Name "installer"');
    expect(guestRunner).not.toContain('Test-VerifiedStage -Name "runtime-lifecycle"');
    expect(packageJson.scripts["dist:prepared"]).toContain("electron-builder");
    expect(packageJson.scripts.dist).toBe(
      "npm run prepare:windows-runtime && npm run dist:prepared",
    );
  });

  it("fails Windows frozen builds before stale outputs can be verified", () => {
    for (const [scriptName, buildAssertion, executableMarker] of [
      ["build-backend.ps1", 'Assert-NativeSuccess "backend PyInstaller build"', "$PackagedBackendExe ="],
      ["build-cli.ps1", 'Assert-NativeSuccess "CLI PyInstaller build"', "$CliExecutable ="],
    ] as const) {
      const script = readFileSync(
        path.resolve("..", "scripts", "build", scriptName),
        "utf8",
      );
      expect(script).toContain("Remove-CleanBuildDirectory");
      expect(script.indexOf(buildAssertion)).toBeGreaterThan(-1);
      expect(script.indexOf(buildAssertion)).toBeLessThan(script.indexOf(executableMarker));
    }
  });

  it("keeps app data cleanup as an opt-in uninstall section", () => {
    const scriptPath = path.resolve("build", "installer.nsh");
    const script = readFileSync(scriptPath, "utf8");
    const packageJson = JSON.parse(readFileSync(path.resolve("package.json"), "utf8")) as {
      name: string;
    };

    expect(existsSync(scriptPath)).toBe(true);
    expect(packageJson.name).toBe("auto-email-sender-desktop");
    expect(script).toContain('Section /o "un.删除本地数据（数据库、材料、缓存和本地配置）"');
    expect(script).not.toContain("是否同时删除本地数据");
    expect(script).not.toContain('Section "un.DeleteAutoEmailSenderAppData"');
    expect(script).toContain("--delete-app-data");
    expect(script).toContain("永久删除 Auto Email Sender 的本地数据");
    expect(script).toContain("MessageBox MB_ICONEXCLAMATION|MB_YESNO|MB_DEFBUTTON2");
    expect(script).toContain(`$APPDATA\\${packageJson.name}`);
    expect(script).toContain("!macro customUnInstallSection");
    expect(script).toContain("un.ConfirmAndDeleteAutoEmailSenderAppData");
    expect(script).toContain("un.DeleteAutoEmailSenderAppDataFromFlag");
  });

  it("cleans up only manifest-owned Agent support during Windows uninstall", () => {
    const installerScript = readFileSync(path.resolve("build", "installer.nsh"), "utf8");
    const cleanupScriptPath = path.resolve("..", "agent-support", "windows-uninstall.ps1");
    const cleanupTestPath = path.resolve(
      "..",
      "scripts",
      "quality",
      "windows-agent-support-cleanup.test.ps1",
    );
    const cleanupScript = readFileSync(cleanupScriptPath, "utf8");
    const workflow = readFileSync(path.resolve("..", ".github", "workflows", "release.yml"), "utf8");

    expect(existsSync(cleanupScriptPath)).toBe(true);
    expect(existsSync(cleanupTestPath)).toBe(true);
    expect(installerScript).toContain("resources\\agent-support\\windows-uninstall.ps1");
    expect(installerScript).toContain("nsExec::ExecToLog");
    expect(cleanupScript).toContain("$expectedCliTargets");
    expect(cleanupScript).toContain("auto-email-sender.cmd");
    expect(cleanupScript).toContain("Remove-PathSafely ([string]$manifestCliTarget)");
    expect(cleanupScript).toContain("$expectedAgentTargets");
    expect(cleanupScript).toContain("claude_code");
    expect(cleanupScript).toContain("copilot_cli");
    expect(cleanupScript).toContain("Remove-ManagedUserPathEntry");
    expect(workflow).toContain("scripts/quality/windows-agent-support-cleanup.test.ps1");
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

  it("keeps only supported Electron locale packs with macOS names", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");
    const macConfig = config.slice(config.indexOf("mac:"), config.indexOf("nsis:"));

    expect(macConfig).toContain(
      "  electronLanguages:\n    - en\n    - en_GB\n    - zh_CN\n    - zh_TW",
    );
    expect(macConfig).not.toContain("- en-US");
    expect(macConfig).not.toContain("- zh-CN");
  });

  it("embeds Sparkle with a signed feed and user-confirmed installation", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");

    expect(config).toContain("SUFeedURL:");
    expect(config).toContain("afterPack: build/configure-sparkle-info.mjs");
    expect(config).toContain("afterSign: build/sanitize-macos-bundle.mjs");
    expect(config).not.toMatch(/^after(?:Pack|Sign): \.\.\//m);
    expect(config).toContain("SUEnableAutomaticChecks: true");
    expect(config).toContain("SUAllowsAutomaticUpdates: false");
    expect(config).toContain("SURequireSignedFeed: true");
    expect(config).toContain("from: native/sparkle/build/Release/sparkle_bridge.node");
    expect(config).toContain("from: native/sparkle/vendor/Sparkle.framework");
    expect(config).toContain("to: Frameworks/Sparkle.framework");
    const hook = readFileSync(
      path.resolve("build", "configure-sparkle-info.mjs"),
      "utf8",
    );
    expect(hook).toContain("process.env.SPARKLE_PUBLIC_ED_KEY");
    expect(hook).toContain('decoded.length !== 32');
    expect(hook).toContain('"-insert", "SUPublicEDKey"');
    const sanitizer = readFileSync(
      path.resolve("build", "sanitize-macos-bundle.mjs"),
      "utf8",
    );
    expect(sanitizer).toContain('"-cr", appPath');
    expect(sanitizer).toContain('"-r", appPath');
    expect(sanitizer).toContain('"--verify", "--deep", "--strict", appPath');
    const setupScript = readFileSync(
      path.resolve("..", "scripts", "build", "setup-sparkle.sh"),
      "utf8",
    );
    expect(setupScript).toContain('sparkle_version="2.9.4"');
    expect(setupScript).toContain("ce89daf967db1e1893ed3ebd67575ed82d3902563e3191ca92aaec9164fbdef9");
  });

  it("keeps platform-specific artifact names", () => {
    const config = readFileSync(path.resolve("electron-builder.yml"), "utf8");

    expect(config).toContain('artifactName: "AutoEmailSender-Setup-${version}.${ext}"');
    expect(config).toContain('artifactName: "AutoEmailSender-${version}-${arch}.${ext}"');
  });

  it("declares platform-specific packaging prerequisites", () => {
    const packageJson = readFileSync(path.resolve("package.json"), "utf8");

    expect(packageJson).toContain('"pack": "npm run prepare:windows-runtime && npm run build && electron-builder --config electron-builder.yml --win --dir"');
    expect(packageJson).toContain('"dist:prepared": "npm run build && electron-builder --config electron-builder.yml --win nsis --publish never"');
    expect(packageJson).toContain('"dist": "npm run prepare:windows-runtime && npm run dist:prepared"');
    expect(packageJson).toContain('"publish": "npm run prepare:windows-runtime && npm run build && electron-builder --config electron-builder.yml --win nsis --publish always"');
    expect(packageJson).toContain('"pack:mac": "npm run prepare:sparkle && npm run build && electron-builder --config electron-builder.yml --mac --dir --publish never"');
    expect(packageJson).toContain('"dist:mac": "npm run prepare:sparkle && npm run build && electron-builder --config electron-builder.yml --mac dmg --publish never"');
    expect(packageJson).not.toContain('"publish:mac"');
  });

  it("publishes both platforms together and uploads appcast last", () => {
    const workflow = readFileSync(path.resolve("..", ".github", "workflows", "release.yml"), "utf8");

    expect(workflow).toContain("build-windows:");
    expect(workflow).toContain("build-macos:");
    expect(workflow).toContain("certify:");
    expect(workflow).toContain("publish:");
    expect(workflow).toContain("needs:");
    expect(workflow).toContain("scripts/release/prepare-sparkle-release.mjs");
    expect(workflow).toContain("python scripts/packaging/package_crawl_mentors_skill.py");
    expect(workflow).toContain("release-assets/skill/*.zip");
    expect(workflow).toContain("if ((${#skill_assets[@]} != 1))");
    expect(workflow).toContain('--json isDraft --jq .isDraft');
    expect(workflow).toContain("Refusing to replace assets on published Release");
    expect(workflow).toContain("workflow_dispatch:");
    expect(workflow).toContain("candidate_run_id:");
    expect(workflow).toContain("release-candidate.mjs candidate");
    expect(workflow).toContain("release-candidate.mjs verify");
    expect(workflow).toContain("run-id: ${{ env.CANDIDATE_RUN_ID }}");
    expect(workflow).toContain("Create deferred release tag");
    expect(workflow.indexOf("Verify certified candidate report and artifact digests")).toBeLessThan(
      workflow.indexOf("Create deferred release tag"),
    );
    expect(workflow.indexOf("Validate staged release artifacts")).toBeLessThan(
      workflow.indexOf("Create deferred release tag"),
    );
    expect(workflow.indexOf('--json isDraft --jq .isDraft')).toBeLessThan(
      workflow.indexOf('gh release edit "$RELEASE_TAG" --notes-file'),
    );
    expect(workflow.indexOf("Refusing to replace assets on published Release")).toBeLessThan(
      workflow.indexOf('gh release upload "$RELEASE_TAG" "${assets[@]}" --clobber'),
    );
    expect(workflow.indexOf("release-assets/skill/*.zip")).toBeLessThan(
      workflow.indexOf("release-assets/macos/appcast.xml --clobber"),
    );
    expect(workflow.indexOf("release-assets/macos/appcast.xml --clobber")).toBeGreaterThan(
      workflow.indexOf('gh release upload "$RELEASE_TAG" "${assets[@]}" --clobber'),
    );
  });
});
