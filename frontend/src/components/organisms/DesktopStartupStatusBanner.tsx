import { AlertCircle, Database, Loader2 } from "lucide-react";
import { useDesktopBackend } from "@/context/DesktopBackendContext";

export const DesktopStartupStatusBanner = () => {
  const { isDesktop, status } = useDesktopBackend();

  if (!isDesktop || !status || status.state === "ready") {
    return null;
  }

  if (status.state === "degraded") {
    return (
      <div className="border-b border-amber-200 bg-amber-50 px-6 py-3 text-sm text-amber-950">
        <div className="mx-auto flex max-w-7xl items-start gap-3">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-none" />
          <div>
            <div className="font-medium">{status.message}</div>
            <div className="mt-1 text-amber-900">
              查询、编辑和其他即时操作仍可正常使用；定时发送、抓取等后台任务恢复后会自动继续。
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (status.state === "error" && status.databaseError?.code === "DATABASE_REQUIRES_NEWER_APP") {
    return (
      <div className="border-b border-red-200 bg-red-50 px-6 py-3 text-sm text-red-950">
        <div className="mx-auto flex max-w-7xl items-start gap-3">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-none" />
          <div className="min-w-0">
            <div className="font-medium">
              当前数据需要 AutoEmailSender {status.databaseError.minimumSupportedAppVersion} 或更高版本
            </div>
            <div className="mt-1 text-red-900">
              请升级到新版继续使用。若必须回退旧版，请从升级前备份恢复数据库。
            </div>
            <div className="mt-2 rounded border border-red-200 bg-white px-2 py-1 font-mono text-xs text-red-900 break-all">
              {status.databaseError.backupDirectory}
            </div>
          </div>
        </div>
      </div>
    );
  }
  if (status.state === "error") {
    return (
      <div className="border-b border-red-200 bg-red-50 px-6 py-3 text-sm text-red-900">
        <div className="mx-auto flex max-w-7xl items-start gap-3">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-none" />
          <div>
            <div className="font-medium">系统准备失败</div>
            <div className="mt-1 text-red-800">
              应用启动时未能完成本地数据检查。请重启应用后再试；如果问题仍然存在，请导出诊断日志反馈。
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (status.state === "restarting") {
    return (
      <div className="border-b border-amber-200 bg-amber-50 px-6 py-3 text-sm text-amber-950">
        <div className="mx-auto flex max-w-7xl items-start gap-3">
          <Loader2 className="mt-0.5 h-4 w-4 flex-none animate-spin" />
          <div>
            <div className="font-medium">正在重启系统服务</div>
            <div className="mt-1 text-amber-900">
              本地服务正在恢复连接，完成后会自动继续。
            </div>
          </div>
        </div>
      </div>
    );
  }

  const secondary = status.verySlowStartup
    ? "如果长时间停留在此状态，可以重启应用；若仍无法恢复，请导出诊断日志反馈。"
    : status.slowStartup
      ? "首次启动或版本升级时可能会稍慢，这不是配置错误。请保持应用打开，完成后会自动恢复。"
      : "新版首次启动可能需要检查或升级本地数据库，通常需要 1-3 分钟。请保持应用打开。";

  return (
    <div className="border-b border-amber-200 bg-amber-50 px-6 py-3 text-sm text-amber-950">
      <div className="mx-auto flex max-w-7xl items-start gap-3">
        {status.phase === "migrating_database" ? (
          <Database className="mt-0.5 h-4 w-4 flex-none" />
        ) : (
          <Loader2 className="mt-0.5 h-4 w-4 flex-none animate-spin" />
        )}
        <div>
          <div className="font-medium">{status.message}</div>
          <div className="mt-1 text-amber-900">{secondary}</div>
        </div>
      </div>
    </div>
  );
};
