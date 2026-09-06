import { Download, FileSpreadsheet } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";
import { ModalShell } from "./formControls";

type Props = {
  exportModalOpen: boolean;
  setExportModalOpen: Dispatch<SetStateAction<boolean>>;
  handleDownloadExport: (format: "xlsx" | "csv") => Promise<void>;
};

export function ProfessorExportDialog({
  exportModalOpen,
  setExportModalOpen,
  handleDownloadExport,
}: Props) {
  return (
    <ModalShell
      open={exportModalOpen}
      title="导出导师信息"
      description="导出全部正常导师，格式与导入模板一致。"
      onClose={() => setExportModalOpen(false)}
    >
      <div className="mt-6 rounded-[28px] border border-stone-200 bg-white p-5 shadow-sm">
        <div className="text-sm font-semibold text-stone-900">选择导出格式</div>
        <p className="mt-2 text-sm leading-6 text-stone-500">
          XLSX 适合表格软件，CSV 适合脚本处理。
        </p>
        <div className="mt-4 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={() => handleDownloadExport("xlsx")}
            className="ui-btn-primary"
          >
            <FileSpreadsheet className="h-4 w-4" />
            导出 XLSX
          </button>
          <button
            type="button"
            onClick={() => handleDownloadExport("csv")}
            className="ui-btn-secondary"
          >
            <Download className="h-4 w-4" />
            导出 CSV
          </button>
        </div>
        <ul className="mt-5 space-y-2 text-sm leading-6 text-stone-600">
          <li>包含全部正常导师，不包含回收站导师。</li>
          <li>导出文件包含个人备注，请谨慎分享。</li>
        </ul>
      </div>
    </ModalShell>
  );
}
