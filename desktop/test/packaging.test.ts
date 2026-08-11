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
    const runtimeStatusScriptPath = path.resolve(
      "build",
      "windows-vc-runtime-status.ps1",
    );
    const runtimeStatusTestPath = path.resolve(
      "..",
      "scripts",
      "quality",
      "windows-vc-runtime-status.test.ps1",
    );
    const runtimeStatusScript = readFileSync(runtimeStatusScriptPath, "utf8");
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
    expect(existsSync(runtimeStatusScriptPath)).toBe(true);
    expect(existsSync(runtimeStatusTestPath)).toBe(true);
    expect(runtimeStatusScript).toContain("RegistryView]::Registry64");
    expect(runtimeStatusScript).toContain("RegistryView]::Registry32");
    expect(runtimeStatusScript).toContain("$_ -ge $requiredVersion");
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
    expect(workflow.indexOf("Prepare Windows packaging prerequisites")).toBeLessThan(
      workflow.indexOf("Test Windows VC++ runtime detection"),
    );
    expect(workflow).toContain("scripts/quality/windows-vc-runtime-status.test.ps1");
    expect(workflow).toContain("build-cli.ps1 -Clean -SkipSync");
    expect(workflow).toContain("build-backend.ps1 -Clean -SkipSync");
    expect(installerScript).toContain("!macro customInstall");
    expect(installerScript).toContain("!macro customCheckAppRunning");
    expect(installerScript).toContain("!insertmacro _CHECK_APP_RUNNING");
    expect(installerScript).toContain("!insertmacro RemovePackagedBrowserRuntime");
    expect(installerScript).toContain("windows-remove-packaged-browser-runtime.ps1");
    expect(installerScript).toContain("windows-vc-runtime-status.ps1");
    expect(installerScript.indexOf("windows-vc-runtime-status.ps1")).toBeLessThan(
      installerScript.indexOf('vc_redist.x64.exe\" /install /quiet /norestart'),
    );
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
    expect(hostRunner).toContain("--prerelease-certification");
    expect(hostRunner).toContain("--candidate-admission");
    expect(hostRunner).toContain("--harness-rehearsal");
    expect(hostRunner).toContain("--inject-interruption-after-previous-install");
    expect(hostRunner).toContain("--require-recovered-stale-state");
    expect(hostRunner).toContain("--normal-soak");
    expect(hostRunner).toContain("--seeded-chaos");
    expect(hostRunner).toContain("-RunNormalSoak");
    expect(hostRunner).toContain("-RunSeededChaos");
    expect(hostRunner).toContain("expected_previous_version");
    expect(hostRunner).toContain("--previous-installer-sha256");
    expect(hostRunner).toContain("--candidate-installer");
    expect(hostRunner).toContain("--candidate-installer-sha256");
    expect(hostRunner).toContain("--candidate-manifest");
    expect(hostRunner).toContain("--candidate-run-id");
    expect(hostRunner).toContain("prerelease-contract.mjs\" latest-stable");
    expect(hostRunner).toContain('git -C "$repo_root" status --porcelain');
    expect(hostRunner).toContain("-ExpectedPreviousVersion");
    expect(hostRunner).toContain("-ExpectedPreviousPackageSha256");
    expect(hostRunner).toContain("-CandidateInstallerPath");
    expect(hostRunner).toContain("-ExpectedCandidatePackageSha256");
    expect(hostRunner).toContain("-CandidateManifestPath");
    expect(hostRunner).toContain("-ExpectedCandidateRunId");
    expect(hostRunner).not.toContain("--skip-runtime-lifecycle");
    expect(hostRunner).toContain("skipping Git bundle transfer");
    expect(hostRunner).toContain("Creating incremental Git bundle");
    expect(hostRunner).toContain('-PreviousRevision "$guest_revision"');
    expect(hostRunner).toContain("AUTO_EMAIL_SENDER_WINDOWS_QA_HOST_TRANSFER_DIR");
    expect(hostRunner).toContain("AUTO_EMAIL_SENDER_WINDOWS_QA_GUEST_TRANSFER_DIR");
    expect(hostRunner).toContain(
      'candidate_installer_name="${candidate_installer##*/}"',
    );
    expect(hostRunner).toContain(
      'transfer_directory_path="$(mktemp -d "$host_transfer_dir/.auto-email-sender-windows-qa.XXXXXX")"',
    );
    expect(hostRunner).toContain(
      'candidate_installer_transfer_path="$transfer_directory_path/$candidate_installer_name"',
    );
    expect(hostRunner).toContain(
      'guest_candidate_installer_path="$guest_transfer_directory_path/$candidate_installer_name"',
    );
    expect(hostRunner).not.toContain(
      'candidate_installer_name="AutoEmailSender-Candidate-$transfer_id.exe"',
    );
    expect(hostRunner).toContain("Test-Path -LiteralPath '$guest_probe_path'");
    expect(hostRunner).toContain("suspend_vm_on_exit=false");
    expect(hostRunner).toContain('prlctl suspend "$vm_name"');
    expect(hostRunner).toContain("Restoring Parallels VM to suspended state");
    expect(hostRunner).not.toContain('$HOME/Desktop');
    expect(hostRunner).not.toContain("Z:/Desktop");
    expect(guestRunner).toContain("[switch]$ForceFull");
    expect(guestRunner).toContain(
      '[ValidateSet("release", "prerelease", "quick", "candidate-admission", "harness-rehearsal")]',
    );
    expect(guestRunner).toContain('[string]$Mode = "release"');
    expect(guestRunner).toContain("[switch]$RunNormalSoak");
    expect(guestRunner).toContain("[switch]$RunSeededChaos");
    expect(guestRunner).toContain("[int]$NormalSoakDurationSeconds = 86400");
    expect(guestRunner).toContain("[int]$SeededChaosDurationSeconds = 28800");
    expect(guestRunner).toContain("[string]$ExpectedRevision");
    expect(guestRunner).toContain("[string]$PreviousRevision");
    expect(guestRunner).toContain("Get-StageFingerprint");
    expect(guestRunner).toContain(
      'ls-tree "--format=%(objectname)" $Revision -- $gitPath',
    );
    expect(guestRunner).not.toContain('rev-parse "${Revision}:$gitPath"');
    expect(guestRunner).toContain("Test-VerifiedStage");
    expect(guestRunner).toContain("Import-LegacyVerifiedStage");
    expect(guestRunner).toContain("toolchainFingerprint");
    expect(guestRunner).toContain("Stop-StaleQaCheckoutProcesses");
    expect(guestRunner).toContain("$IsPackagedPreflight");
    expect(guestRunner).toContain("if (-not $IsPackagedPreflight)");
    expect(guestRunner).toContain("Test-QaExecutableTimeoutRecovery");
    expect(guestRunner).toContain("RequireRecoveredStaleState");
    expect(guestRunner).toContain("Get-ValidatedHarnessSeedCheckpoint");
    expect(guestRunner).toContain("Reusing validated previous-stable harness seed checkpoint");
    expect(guestRunner).toContain("Previous-stable install and seed are already validated");
    expect(guestRunner).toContain('$evidenceRoot = Join-Path $qaBase "e-$qaTimestamp"');
    expect(guestRunner).toContain("Packaged QA lifecycle evidence path exceeds the Windows path budget");
    expect(guestRunner).toContain("previous_executable_sha256");
    expect(guestRunner).toContain("foreign_key_violations");
    expect(guestRunner).toContain("Add-QaHarnessInstallerRegistration");
    expect(guestRunner).toContain("must leave exactly one scoped installer registration");
    expect(guestRunner).toContain("qa-stale-process-probe.exe");
    expect(guestRunner).toContain('Test-VerifiedStage -Name "backend-suite"');
    expect(guestRunner).toContain(
      'Test-VerifiedStage -Name "backend-release-contracts"',
    );
    expect(guestRunner).toContain(
      'Test-VerifiedStage -Name "release-orchestration-contracts"',
    );
    expect(guestRunner).toContain("prepare-release.test.ps1");
    expect(guestRunner).toContain("release-script.test.ps1");
    expect(guestRunner).toContain("prerelease-script.test.ps1");
    expect(guestRunner).toContain("test.test_backend_build_script");
    expect(guestRunner).toContain('Invoke-QaStep "Windows installer build"');
    expect(guestRunner).toContain("if ($IsFormal)");
    expect(guestRunner).toContain("npm run dist:prepared");
    expect(guestRunner).toContain(
      'Invoke-QaStep "Installed packaged split lifecycle and optional soak certification"',
    );
    expect(guestRunner).toContain("packaged-runtime-qa.py");
    expect(guestRunner).toContain("PreviousInstallerPath");
    expect(guestRunner).toContain("ExpectedPreviousVersion");
    expect(guestRunner).toContain("ExpectedPreviousPackageSha256");
    expect(guestRunner).toContain("CandidateInstallerPath");
    expect(guestRunner).toContain("ExpectedCandidatePackageSha256");
    expect(guestRunner).toContain("CandidateManifestPath");
    expect(guestRunner).toContain("ExpectedCandidateRunId");
    expect(guestRunner).toContain('release-candidate.mjs") `');
    expect(guestRunner).toContain("asset `");
    expect(guestRunner).toContain("seed-previous-packaged-upgrade.py");
    expect(guestRunner).toContain("--package-file $previousInstallerPathLocal");
    expect(guestRunner).toContain(
      "Copy-QaPackage -Source $CandidateInstallerPath -Destination $candidateInstallerPathLocal",
    );
    expect(guestRunner).toContain(
      "Copy-QaPackage -Source $CandidateManifestPath -Destination $candidateManifestPathLocal",
    );
    expect(guestRunner).toContain("function Copy-QaPackage");
    expect(guestRunner).toContain("$sourcePath.Equals($destinationPath");
    expect(guestRunner).toContain("$startInfo.Arguments = $Arguments");
    expect(guestRunner).toContain("$startInfo.EnvironmentVariables[");
    expect(guestRunner).toContain("[int]$TimeoutSeconds = 600");
    expect(guestRunner).toContain("$deadline = [datetime]::UtcNow.AddSeconds($TimeoutSeconds)");
    expect(guestRunner).toContain("while (-not $process.WaitForExit(500))");
    expect(guestRunner).toContain("Timed-out process tree:");
    expect(guestRunner).toContain("Unexpected-window process tree:");
    expect(guestRunner).toContain("[switch]$RejectVisibleWindow");
    expect(guestRunner).toContain("displayed an unexpected window during silent execution");
    expect(guestRunner.match(/-RejectVisibleWindow/g)?.length).toBeGreaterThanOrEqual(5);
    expect(guestRunner).toContain("Get-QaVcRedistTimeoutDiagnostic");
    expect(guestRunner).toContain("VC++ Burn timeout diagnostic:");
    expect(guestRunner).not.toContain("CommandLine = [string]$_.CommandLine");
    expect(guestRunner).toContain("taskkill.exe /PID $process.Id /T /F");
    expect(guestRunner).toContain("$previousInstallerTimeoutSeconds = 600");
    expect(guestRunner).toContain("$candidateInstallerTimeoutSeconds = 300");
    expect(guestRunner).toContain(
      "$uninstallerTimeoutSeconds = if ($IsPackagedPreflight) { 120 } else { 600 }",
    );
    expect(guestRunner).toContain("-TimeoutSeconds $uninstallerTimeoutSeconds");
    expect(guestRunner).toContain("Get-QaInstallerRegistrations");
    expect(guestRunner).toContain("Remove-QaInstallerRegistrations");
    expect(guestRunner).not.toContain(
      "Remove-Item -LiteralPath $registration.InstallRoot -Recurse -Force",
    );
    expect(guestRunner).toContain(
      '"HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall"',
    );
    expect(guestRunner).toContain(
      "Remove-QaInstallerRegistrations -QaBasePath $qaBase -InstallRoot $installRoot",
    );
    expect(guestRunner).not.toContain("$startInfo.ArgumentList");
    expect(guestRunner).not.toContain("$startInfo.Environment[");
    expect(guestRunner).toContain('-Arguments "/S /D=$installRoot"');
    expect(guestRunner).toContain('"--existing-user-data", $upgradeUserData');
    expect(guestRunner).toContain('"--upgrade-manifest", $upgradeManifest');
    expect(guestRunner).toContain(
      '"--expected-previous-version", $ExpectedPreviousVersion',
    );
    expect(guestRunner).toContain('"--system-sleep-wake"');
    expect(guestRunner).toContain('"--artifact-root", $installRoot');
    expect(guestRunner).toContain(
      '"--package-file", $candidateInstallerPathLocal',
    );
    expect(guestRunner).toContain(
      '"--expected-app-version", ([string]$desktopPackage.version)',
    );
    expect(guestRunner).toContain(
      '"--expected-package-sha256", $installerSha256',
    );
    expect(guestRunner).toContain(
      '"--candidate-manifest-file", $candidateManifestPathLocal',
    );
    expect(guestRunner).toContain(
      '"--expected-candidate-run-id", ([string]$ExpectedCandidateRunId)',
    );
    expect(guestRunner).toContain(
      '"--previous-package-file", $previousInstallerPathLocal',
    );
    expect(guestRunner).toContain(
      '"--expected-previous-package-sha256", $ExpectedPreviousPackageSha256',
    );
    expect(guestRunner).toContain('"--certification"');
    expect(guestRunner).toContain('"--prerelease-certification"');
    expect(guestRunner).toContain('"--candidate-admission"');
    expect(guestRunner).toContain('"--harness-rehearsal"');
    expect(guestRunner).toContain("non-certifying-candidate-admission");
    expect(guestRunner).toContain("non-certifying-harness-rehearsal");
    expect(guestRunner).toContain('"AUTO_EMAIL_SENDER_PACKAGED_QA"');
    expect(guestRunner).toContain('Join-Path $installRoot "Uninstall Auto Email Sender.exe"');
    expect(guestRunner).toContain("Uninstall did not preserve isolated user data");
    expect(guestRunner).toContain("repeat candidate Windows installer");
    expect(guestRunner).toMatch(/Windows packaged QA artifacts:[\s\S]*?finally \{[\s\S]*?Stop-QaProcessesFromRoot/);
    expect(guestRunner).toContain("it is not valid release preflight evidence");
    expect(guestRunner).not.toContain('Test-VerifiedStage -Name "installer"');
    expect(guestRunner).not.toContain('Test-VerifiedStage -Name "runtime-lifecycle"');
    expect(packageJson.scripts["dist:prepared"]).toContain("electron-builder");
    expect(packageJson.scripts.dist).toBe(
      "npm run prepare:windows-runtime && npm run dist:prepared",
    );
  });

  it("provides a real macOS DMG lifecycle and soak certification entrypoint", () => {
    const runnerPath = path.resolve(
      "..",
      "scripts",
      "quality",
      "run-macos-packaged-qa.sh",
    );
    const runner = readFileSync(runnerPath, "utf8");

    expect(existsSync(runnerPath)).toBe(true);
    expect(runner).toContain("--certification");
    expect(runner).toContain("--prerelease-certification");
    expect(runner).toContain("--candidate-admission");
    expect(runner).toContain("--harness-rehearsal");
    expect(runner).toContain("--inject-interruption-after-previous-install");
    expect(runner).toContain("--require-clean-rehearsal-state");
    expect(runner).toContain("--development-smoke");
    expect(runner).toContain("SPARKLE_PUBLIC_ED_KEY");
    expect(runner).not.toContain('echo "$SPARKLE_PUBLIC_ED_KEY"');
    expect(runner).toContain("hdiutil attach -readonly -nobrowse -plist");
    expect(runner).toContain('ditto "${MountedApps[0]}" "$InstalledBundle"');
    expect(runner).toContain("codesign --verify --deep --strict");
    expect(runner).toContain("packaged-runtime-qa.py");
    expect(runner).toContain("run_with_timeout 120 hdiutil attach");
    expect(runner).toContain("run_with_timeout 1200 uv run");
    expect(runner).toContain("--previous-dmg");
    expect(runner).toContain("--expected-dmg-sha256");
    expect(runner).toContain("--expected-previous-dmg-sha256");
    expect(runner).toContain("--candidate-manifest");
    expect(runner).toContain("--candidate-run-id");
    expect(runner).toContain("ExpectedPreviousVersion");
    expect(runner).toContain("prerelease-contract.mjs\" latest-stable");
    expect(runner).toContain("--dedicated-test-account");
    expect(runner).toContain("seed-previous-packaged-upgrade.py");
    expect(runner).toContain('--package-file "$PreviousDmgPath"');
    expect(runner).toContain("--existing-user-data");
    expect(runner).toContain("--upgrade-manifest");
    expect(runner).toContain("--expected-previous-version");
    expect(runner).toContain("--expected-app-version");
    expect(runner).toContain('--package-file "$DmgPath"');
    expect(runner).toContain('--expected-package-sha256 "$ExpectedDmgSha256"');
    expect(runner).toContain("--previous-package-file");
    expect(runner).toContain(
      '--expected-previous-package-sha256 "$ExpectedPreviousDmgSha256"',
    );
    expect(runner).toContain(
      '--candidate-manifest-file "$CandidateManifestPath"',
    );
    expect(runner).toContain(
      '--expected-candidate-run-id "$CandidateRunId"',
    );
    expect(runner).toContain('echo "macOS 正式认证的全部场景都必须使用 --dmg');
    expect(runner).toContain("--system-sleep-wake");
    expect(runner).toContain("/usr/bin/sudo -n /usr/bin/true");
    expect(runner).toContain("sudo -v");
    expect(runner).toContain('--artifact-root "$AppBundle"');
    expect(runner).toContain('mv "$InstalledBundle" "$UninstalledBundle"');
    expect(runner).toContain("卸载模拟后隔离用户数据库未保留");
    expect(runner).toContain("non-certifying-candidate-admission");
    expect(runner).toContain("non-certifying-harness-rehearsal");
    expect(runner).toContain("重复安装没有恢复 app bundle");
  });

  it("gates packaged QA isolation before importing desktop bootstrap", () => {
    const mainSource = readFileSync(path.resolve("src", "main.ts"), "utf8");
    const qaGate = readFileSync(
      path.resolve("src", "main", "packaged-qa", "user-data.ts"),
      "utf8",
    );
    const applicationSource = readFileSync(
      path.resolve("src", "main", "bootstrap", "application.ts"),
      "utf8",
    );
    const updateSource = readFileSync(
      path.resolve("src", "main", "updates", "service.ts"),
      "utf8",
    );
    const backendServiceSource = readFileSync(
      path.resolve("src", "main", "backend", "service.ts"),
      "utf8",
    );

    expect(mainSource.indexOf("configurePackagedQaUserData(app)")).toBeLessThan(
      mainSource.indexOf('import("./main/bootstrap/application.js")'),
    );
    expect(mainSource).not.toContain(
      'import { bootstrapDesktopApplication } from "./main/bootstrap/application.js"',
    );
    expect(qaGate).toContain("enabled-for-release-certification");
    expect(qaGate).toContain("PACKAGED_QA_SENTINEL_NAME");
    expect(qaGate).toContain("must not traverse symbolic links");
    expect(applicationSource).toContain("getActivePackagedQaIsolatedHomePath");
    expect(applicationSource).toContain("getPackagedQaDiagnosticsExportPath");
    expect(applicationSource).toContain("exportPackagedQaDiagnosticsOnce");
    expect(updateSource).toContain("getActivePackagedQaUserDataPath");
    expect(applicationSource).toContain('powerMonitor.on("resume"');
    expect(applicationSource).toContain("backend?.notifySystemResume?.()");
    expect(backendServiceSource).toContain("notifySystemResume(): void");
    expect(backendServiceSource).toContain(
      "this.#lastWorkerHeartbeatAdvancedAt = performance.now()",
    );
  });

  it("uses the embedded release identity as the packaged backend default", () => {
    const application = readFileSync(
      path.resolve("src", "main", "bootstrap", "application.ts"),
      "utf8",
    );
    expect(application).toContain(
      "releaseDefaultMode: releaseBuildIdentity.defaultBackendMode",
    );
    expect(application).toContain("release_identity_fallback");
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
    const runtimeCleanupScriptPath = path.resolve(
      "build",
      "windows-remove-packaged-browser-runtime.ps1",
    );
    const cleanupTestPath = path.resolve(
      "..",
      "scripts",
      "quality",
      "windows-agent-support-cleanup.test.ps1",
    );
    const cleanupScript = readFileSync(cleanupScriptPath, "utf8");
    const runtimeCleanupScript = readFileSync(runtimeCleanupScriptPath, "utf8");
    const workflow = readFileSync(path.resolve("..", ".github", "workflows", "release.yml"), "utf8");

    expect(existsSync(cleanupScriptPath)).toBe(true);
    expect(existsSync(runtimeCleanupScriptPath)).toBe(true);
    expect(existsSync(cleanupTestPath)).toBe(true);
    expect(installerScript).toContain("resources\\agent-support\\windows-uninstall.ps1");
    expect(installerScript).toContain("RemovePackagedBrowserRuntime");
    expect(installerScript).toContain("nsExec::ExecToLog");
    expect(cleanupScript).toContain("$expectedCliTargets");
    expect(cleanupScript).toContain("auto-email-sender.cmd");
    expect(cleanupScript).toContain("Remove-PathSafely ([string]$manifestCliTarget)");
    expect(cleanupScript).toContain("$expectedAgentTargets");
    expect(cleanupScript).toContain("claude_code");
    expect(cleanupScript).toContain("copilot_cli");
    expect(cleanupScript).toContain("Remove-ManagedUserPathEntry");
    expect(runtimeCleanupScript).toContain('"\\\\?\\"');
    expect(runtimeCleanupScript).toContain("[System.IO.Directory]::EnumerateFileSystemEntries");
    expect(runtimeCleanupScript).toContain("[System.IO.File]::Delete");
    expect(runtimeCleanupScript).toContain("inside the packaged browser runtime");
    expect(runtimeCleanupScript).not.toContain(
      "[System.IO.Directory]::Delete($extendedBrowserRuntime, -not $runtimeIsReparsePoint)",
    );
    expect(runtimeCleanupScript).not.toContain("cmd.exe");
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
