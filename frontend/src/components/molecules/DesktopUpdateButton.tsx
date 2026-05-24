import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import { Loader2, RefreshCw, X } from "lucide-react";
import { useNotification } from "@/context/NotificationContext";
import {
  checkForDesktopUpdate,
  downloadDesktopUpdate,
  getDesktopAppVersion,
  installDownloadedDesktopUpdate,
  isDesktopApp,
  onDesktopUpdateStatus,
  switchDesktopUpdateToFullDownload,
} from "@/lib/desktopApi";
import { useDismissableLayerClick } from "@/lib/useDismissableLayerClick";
import type { DesktopUpdateDownloadMode, DesktopUpdateStatus } from "@/types/desktop";

const PENDING_UPDATE_KEY = "desktop_pending_update_version";

const readPendingVersion = (): string | null => {
  const value = window.localStorage.getItem(PENDING_UPDATE_KEY);
  return value && value.trim() ? value.trim() : null;
};

const writePendingVersion = (version: string | null) => {
  if (version) {
    window.localStorage.setItem(PENDING_UPDATE_KEY, version);
    return;
  }
  window.localStorage.removeItem(PENDING_UPDATE_KEY);
};

const CONNECTION_ERROR_PATTERNS = [
  "connection error",
  "econnreset",
  "econnrefused",
  "etimedout",
  "enotfound",
  "network offline",
];

function formatUpdateCheckErrorMessage(message: string): string {
  const normalizedMessage = message.toLowerCase();
  if (!CONNECTION_ERROR_PATTERNS.some((pattern) => normalizedMessage.includes(pattern))) {
    return message;
  }
  return `${message}。请检查系统代理是否已开启，或确认当前网络可以访问 GitHub。`;
}

function getUpdateStatusKey(status: DesktopUpdateStatus): string {
  return JSON.stringify(status);
}

export function DesktopUpdateButton() {
  if (!isDesktopApp()) {
    return null;
  }

  return <DesktopUpdateButtonInner />;
}

function DesktopUpdateButtonInner() {
  const [version, setVersion] = useState<string>("加载中");
  const [status, setStatus] = useState<DesktopUpdateStatus | null>(null);
  const [pendingVersion, setPendingVersion] = useState<string | null>(() =>
    typeof window === "undefined" ? null : readPendingVersion(),
  );
  const [releaseDialogStatus, setReleaseDialogStatus] = useState<
    Extract<DesktopUpdateStatus, { state: "available" }> | null
  >(null);
  const [checking, setChecking] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const emittedStatusDuringCheckRef = useRef<string | null>(null);
  const { notifyError, notifySuccess } = useNotification();

  const handleStatus = useCallback(
    (status: DesktopUpdateStatus) => {
      setStatus(status);

      if (status.state === "checking") {
        setChecking(true);
        return;
      }

      if (status.state === "available") {
        setChecking(false);
        setPendingVersion(status.nextVersion);
        writePendingVersion(status.nextVersion);
        setReleaseDialogStatus(status);
        return;
      }

      if (status.state === "not_available") {
        setChecking(false);
        setDownloading(false);
        setReleaseDialogStatus(null);
        if (pendingVersion !== null && pendingVersion === version) {
          writePendingVersion(null);
          setPendingVersion(null);
          return;
        }
        notifySuccess("检查更新", "当前已是最新版本。");
        return;
      }

      if (status.state === "downloading" || status.state === "slow_download_offered") {
        setChecking(false);
        setDownloading(true);
        return;
      }

      if (status.state === "downloaded_pending_install") {
        setChecking(false);
        setDownloading(false);
        setPendingVersion(status.nextVersion);
        writePendingVersion(status.nextVersion);
        return;
      }

      if (status.state === "installing") {
        setChecking(false);
        setDownloading(false);
        return;
      }

      if (status.state === "error") {
        setChecking(false);
        setDownloading(false);
        notifyError("检查更新失败", formatUpdateCheckErrorMessage(status.message));
      }
    },
    [notifyError, notifySuccess, pendingVersion, version],
  );

  useEffect(() => {
    let cancelled = false;
    void getDesktopAppVersion()
      .then((appVersion) => {
        if (cancelled) {
          return;
        }

        setVersion(appVersion);
        const storedPendingVersion = readPendingVersion();
        if (storedPendingVersion === appVersion) {
          writePendingVersion(null);
          setPendingVersion(null);
          return;
        }

        setPendingVersion(storedPendingVersion);
      })
      .catch(() => {
        if (!cancelled) {
          setVersion("未知");
        }
      });

    const unsubscribe = onDesktopUpdateStatus((nextStatus) => {
      emittedStatusDuringCheckRef.current = getUpdateStatusKey(nextStatus);
      handleStatus(nextStatus);
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [handleStatus]);

  const hasPendingUpdate = useMemo(
    () => pendingVersion !== null && pendingVersion !== version,
    [pendingVersion, version],
  );

  const startDownload = useCallback(
    async (mode: DesktopUpdateDownloadMode) => {
      if (downloading) {
        return;
      }

      setDownloading(true);
      try {
        const downloadStatus = await downloadDesktopUpdate(mode);
        handleStatus(downloadStatus);
      } catch (downloadError) {
        const message = downloadError instanceof Error ? downloadError.message : "下载更新失败";
        notifyError("更新下载失败", message);
      } finally {
        setDownloading(false);
      }
    },
    [downloading, handleStatus, notifyError],
  );

  const switchToFullDownload = useCallback(async () => {
    setDownloading(true);
    try {
      const downloadStatus = await switchDesktopUpdateToFullDownload();
      handleStatus(downloadStatus);
    } catch (downloadError) {
      const message = downloadError instanceof Error ? downloadError.message : "切换全量下载失败";
      notifyError("切换全量下载失败", message);
    } finally {
      setDownloading(false);
    }
  }, [handleStatus, notifyError]);

  const installUpdate = useCallback(async () => {
    try {
      await installDownloadedDesktopUpdate();
    } catch (installError) {
      const message = installError instanceof Error ? installError.message : "启动安装失败";
      notifyError("启动安装失败", message);
    }
  }, [notifyError]);

  const handleCheckUpdateWithInstall = useCallback(async () => {
    if (checking || downloading) {
      return;
    }

    setChecking(true);
    emittedStatusDuringCheckRef.current = null;

    try {
      const status = await checkForDesktopUpdate();
      if (emittedStatusDuringCheckRef.current !== getUpdateStatusKey(status)) {
        handleStatus(status);
      }
      if (status.state === "downloaded_pending_install") {
        await installUpdate();
      }
    } catch (checkError) {
      const message = checkError instanceof Error ? checkError.message : "检查更新失败";
      notifyError("检查更新失败", formatUpdateCheckErrorMessage(message));
    } finally {
      setChecking(false);
    }
  }, [checking, downloading, handleStatus, installUpdate, notifyError]);

  const isBusy = checking || downloading;

  return (
    <>
      <span className="inline-flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={isBusy}
          onClick={() => void handleCheckUpdateWithInstall()}
          className="inline-flex min-h-[2.8rem] min-w-[11rem] items-center gap-2 rounded-2xl border border-stone-200/90 bg-white/92 px-3 py-2 text-left text-stone-700 shadow-sm shadow-stone-200/45 backdrop-blur-sm transition hover:border-stone-300 hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/15 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary/8 text-primary">
            {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          </span>
          <span className="flex min-w-0 flex-1 flex-col items-start leading-none">
            <span className="text-[11px] font-medium tracking-[0.03em] text-stone-500">
              v{version}
            </span>
            <span className="mt-1 text-[13px] font-medium text-stone-900">
              检查更新
            </span>
          </span>
          {hasPendingUpdate ? (
            <span className="shrink-0 rounded-full bg-rose-600 px-2 py-0.5 text-[10px] font-semibold tracking-[0.08em] text-white">
              NEW
            </span>
          ) : null}
          {!hasPendingUpdate && isBusy ? (
            <span className="shrink-0 rounded-full border border-stone-200 bg-stone-50 px-2 py-0.5 text-[10px] font-medium text-stone-500">
              ...
            </span>
          ) : null}
        </button>
        <DesktopUpdateStatusBar
          status={status}
          onStartDownload={startDownload}
          onSwitchToFullDownload={switchToFullDownload}
          onInstall={installUpdate}
        />
        <DesktopUpdateReleaseNotesDialog
          status={releaseDialogStatus}
          onClose={() => setReleaseDialogStatus(null)}
          onStartDownload={(mode) => {
            setReleaseDialogStatus(null);
            void startDownload(mode);
          }}
        />
      </span>
    </>
  );
}

function DesktopUpdateReleaseNotesDialog({
  status,
  onClose,
  onStartDownload,
}: {
  status: Extract<DesktopUpdateStatus, { state: "available" }> | null;
  onClose: () => void;
  onStartDownload: (mode: DesktopUpdateDownloadMode) => void;
}) {
  const {
    onBackdropClick,
    onBackdropMouseDown,
    onContentClick,
    onContentMouseDown,
  } =
    useDismissableLayerClick(onClose);

  if (status === null) {
    return null;
  }

  const releaseNotes = status.releaseNotes?.trim() || "新版本已发布，更新内容暂不可用。";

  return createPortal(
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-stone-950/35 p-4 backdrop-blur-md"
      role="presentation"
      onClick={onBackdropClick}
      onMouseDown={onBackdropMouseDown}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="desktop-update-release-title"
        className="w-full max-w-2xl overflow-hidden rounded-[28px] border border-stone-200/80 bg-white shadow-[0_34px_90px_-32px_rgba(41,37,36,0.55)]"
        onClick={onContentClick}
        onMouseDown={onContentMouseDown}
      >
        <div className="flex items-start justify-between gap-4 border-b border-stone-100 px-6 py-5">
          <div>
            <h3 id="desktop-update-release-title" className="text-lg font-semibold text-stone-900">
              发现新版本 v{status.nextVersion}
            </h3>
            <p className="mt-1 text-sm text-stone-500">
              当前 v{status.version} -&gt; v{status.nextVersion}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-stone-300 hover:text-stone-900"
            aria-label="关闭更新公告"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div
          data-testid="desktop-update-release-notes"
          className="max-h-[50vh] overflow-y-auto px-6 py-5"
        >
          <article className={RELEASE_NOTES_MARKDOWN_CLASS_NAME}>
            <ReactMarkdown>{releaseNotes}</ReactMarkdown>
          </article>
        </div>
        <div className="flex flex-wrap justify-end gap-3 border-t border-stone-100 px-6 py-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm font-medium text-stone-700 transition hover:border-stone-300 hover:bg-stone-50"
          >
            稍后
          </button>
          <button
            type="button"
            onClick={() => onStartDownload("full")}
            className="rounded-2xl border border-stone-200 bg-white px-4 py-2.5 text-sm font-semibold text-stone-700 transition hover:border-primary/40 hover:text-primary"
          >
            全量下载
          </button>
          <button
            type="button"
            onClick={() => onStartDownload("differential")}
            className="rounded-2xl bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm shadow-primary/20 transition hover:bg-primary/90"
          >
            差量下载
          </button>
        </div>
      </section>
    </div>
    ,
    document.body,
  );
}

const RELEASE_NOTES_MARKDOWN_CLASS_NAME =
  "space-y-4 break-words text-sm leading-7 text-stone-700 " +
  "[&_h1]:text-2xl [&_h1]:font-semibold [&_h1]:leading-tight [&_h1]:tracking-[0.01em] [&_h1]:text-stone-900 " +
  "[&_h2]:mt-6 [&_h2]:text-lg [&_h2]:font-semibold [&_h2]:leading-tight [&_h2]:tracking-[0.01em] [&_h2]:text-stone-900 " +
  "[&_h3]:mt-5 [&_h3]:text-base [&_h3]:font-semibold [&_h3]:leading-tight [&_h3]:text-stone-900 " +
  "[&_p]:m-0 [&_p]:leading-7 " +
  "[&_ul]:my-0 [&_ul]:list-disc [&_ul]:space-y-2 [&_ul]:pl-5 " +
  "[&_ol]:my-0 [&_ol]:list-decimal [&_ol]:space-y-2 [&_ol]:pl-5 " +
  "[&_li]:leading-7 " +
  "[&_strong]:font-semibold [&_strong]:text-stone-900 " +
  "[&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2 " +
  "[&_code]:rounded-md [&_code]:bg-stone-100 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.92em] [&_code]:text-stone-900 " +
  "[&_pre]:overflow-x-auto [&_pre]:rounded-2xl [&_pre]:bg-stone-950 [&_pre]:p-4 [&_pre]:text-stone-100 " +
  "[&_pre_code]:rounded-none [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-inherit " +
  "[&_blockquote]:border-l-4 [&_blockquote]:border-stone-200 [&_blockquote]:pl-4 [&_blockquote]:text-stone-500";

function DesktopUpdateStatusBar({
  status,
  onStartDownload,
  onSwitchToFullDownload,
  onInstall,
}: {
  status: DesktopUpdateStatus | null;
  onStartDownload: (mode: DesktopUpdateDownloadMode) => void;
  onSwitchToFullDownload: () => void;
  onInstall: () => void;
}) {
  if (status === null) {
    return null;
  }

  if (status.state === "available") {
    return (
      <span className="inline-flex min-h-[2.8rem] items-center gap-2 rounded-xl border border-stone-200 bg-white px-3 py-2 text-xs text-stone-600 shadow-sm">
        <span className="font-medium text-stone-800">发现 v{status.nextVersion}</span>
        <span>差量下载：开始后显示实际大小</span>
        <span>全量约 {formatBytes(status.fullDownloadBytes ?? 0)}</span>
        <button
          type="button"
          onClick={() => onStartDownload("full")}
          className="rounded-lg border border-stone-200 px-2 py-1 text-stone-700 transition hover:border-primary/40 hover:text-primary"
        >
          全量下载
        </button>
        <button
          type="button"
          onClick={() => onStartDownload("differential")}
          className="rounded-lg bg-primary px-2 py-1 text-white transition hover:bg-primary/90"
        >
          差量下载
        </button>
      </span>
    );
  }

  if (status.state === "downloading" || status.state === "slow_download_offered") {
    const modeLabel = status.mode === "full" ? "全量包" : "增量包";

    return (
      <span
        className="inline-flex min-w-[18rem] flex-col rounded-xl border border-stone-200 bg-white px-3 py-2 text-xs text-stone-600 shadow-sm"
        aria-label="更新下载进度"
      >
        <span className="font-medium text-stone-800">
          {modeLabel}：总计 {formatBytes(status.totalBytes)}
        </span>
        <span className="h-1.5 overflow-hidden rounded-full bg-stone-100">
          <span className="block h-full bg-primary" style={{ width: `${status.percent}%` }} />
        </span>
        <span className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
          <span>{modeLabel}：已下载 {formatBytes(status.transferredBytes)}</span>
          <span>{modeLabel}：剩余 {formatBytes(status.remainingBytes)}</span>
          <span>{formatBytes(status.bytesPerSecond)}/s</span>
          <span>{formatEta(status.remainingSeconds)}</span>
        </span>
        {status.state === "slow_download_offered" ? (
          <span className="mt-2 flex flex-wrap items-center gap-2">
            <span>
              全量约 {formatBytes(status.fullDownloadBytes ?? status.totalBytes)}，已消耗{" "}
              {formatBytes(status.transferredBytes)}
            </span>
            <button
              type="button"
              onClick={onSwitchToFullDownload}
              className="rounded-lg bg-primary px-2 py-1 text-white transition hover:bg-primary/90"
            >
              切换全量下载
            </button>
          </span>
        ) : null}
      </span>
    );
  }

  if (status.state === "downloaded_pending_install") {
    return (
      <span className="inline-flex min-h-[2.8rem] items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
        <span>v{status.nextVersion} 已下载，可稍后安装；再次检查更新会直接安装。</span>
        <button
          type="button"
          onClick={onInstall}
          className="rounded-lg bg-emerald-700 px-2 py-1 text-white transition hover:bg-emerald-800"
        >
          立即重启安装
        </button>
      </span>
    );
  }

  return null;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const kb = bytes / 1024;
  if (kb < 1024) {
    return `${kb.toFixed(1)} KB`;
  }
  return `${(kb / 1024).toFixed(1)} MB`;
}

function formatEta(seconds: number | null): string {
  if (seconds === null) {
    return "预计时间未知";
  }
  if (seconds < 60) {
    return `预计 ${seconds} 秒`;
  }
  return `预计 ${Math.ceil(seconds / 60)} 分钟`;
}
