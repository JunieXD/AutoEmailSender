import { openExternalHttpUrl } from "@/lib/externalUrls";
import type { ProfessorImportFileResultDTO } from "@/types";
import {
  Download,
  ExternalLink,
  FileSpreadsheet,
  Loader2,
  Upload,
} from "lucide-react";
import type { Dispatch, SetStateAction } from "react";
import {
  type ChangeEvent,
  type DragEvent as ReactDragEvent,
  type MouseEvent as ReactMouseEvent,
} from "react";
import { ModalShell } from "./formControls";
const MENTOR_CRAWLER_SKILL_GUIDE_URL =
  "https://juniexd.github.io/AutoEmailSender/docs/mentor-crawler-skill";

type Props = {
  importModalOpen: boolean;
  importingFile: boolean;
  setImportModalOpen: Dispatch<SetStateAction<boolean>>;
  handleDownloadTemplate: (format: "xlsx" | "csv") => Promise<void>;
  handleImportDropZoneClick: (event: ReactMouseEvent<HTMLLabelElement>) => void;
  handleDropImportFile: (event: ReactDragEvent<HTMLLabelElement>) => void;
  handleChooseImportFile: (event: ChangeEvent<HTMLInputElement>) => void;
  importFile: File | null;
  importResult: ProfessorImportFileResultDTO | null;
  setImportResult: Dispatch<
    SetStateAction<ProfessorImportFileResultDTO | null>
  >;
  setImportFile: Dispatch<SetStateAction<File | null>>;
  handleImportSubmit: () => Promise<void>;
};

export function ProfessorImportDialog({
  importModalOpen,
  importingFile,
  setImportModalOpen,
  handleDownloadTemplate,
  handleImportDropZoneClick,
  handleDropImportFile,
  handleChooseImportFile,
  importFile,
  importResult,
  setImportResult,
  setImportFile,
  handleImportSubmit,
}: Props) {
  return (
    <ModalShell
      open={importModalOpen}
      title="导入导师文件"
      description="按邮箱匹配并更新；回收站记录会自动恢复。"
      onClose={() => {
        if (importingFile) {
          return;
        }
        setImportModalOpen(false);
      }}
    >
      <div className="mt-6 grid gap-6 lg:grid-cols-[0.95fr,1.05fr]">
        <div className="rounded-[28px] border border-stone-200 bg-white p-5 shadow-sm">
          <div className="text-sm font-semibold text-stone-900">先下载模板</div>
          <p className="mt-2 text-sm leading-6 text-stone-500">
            支持 CSV 和 XLSX。
          </p>
          <div className="mt-4 flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => handleDownloadTemplate("xlsx")}
              className="ui-btn-primary"
            >
              <FileSpreadsheet className="h-4 w-4" />
              下载 XLSX 模板
            </button>
            <button
              type="button"
              onClick={() => handleDownloadTemplate("csv")}
              className="ui-btn-secondary"
            >
              <Download className="h-4 w-4" />
              下载 CSV 模板
            </button>
          </div>
          <button
            type="button"
            onClick={() => openExternalHttpUrl(MENTOR_CRAWLER_SKILL_GUIDE_URL)}
            className="mt-4 inline-flex items-center gap-2 text-left text-sm font-medium text-primary transition hover:text-primary/80"
          >
            <ExternalLink className="h-4 w-4" />用 Codex / Claude Code
            从导师官网生成导入表
          </button>
          <ul className="mt-5 space-y-2 text-sm leading-6 text-stone-600">
            <li>必填列为 name 和 email；格式错误的行会跳过。</li>
            <li>省略标签或个人备注列时，已有内容不会被清空。</li>
            <li>
              <span className="font-mono text-xs">research_direction</span>{" "}
              多个方向用中文分号；分隔。
            </li>
            <li>
              <span className="font-mono text-xs">recent_papers</span>{" "}
              多篇论文用 | 分隔，最多保留前 8 篇。
            </li>
          </ul>
        </div>

        <div className="rounded-[28px] border border-stone-200 bg-white p-5 shadow-sm">
          <div className="text-sm font-semibold text-stone-900">上传并导入</div>
          <p className="mt-2 text-sm leading-6 text-stone-500">
            同邮箱记录将更新；新邮箱将新增。
          </p>
          <label
            onClick={handleImportDropZoneClick}
            onDragOver={(event) => event.preventDefault()}
            onDrop={handleDropImportFile}
            className="mt-4 flex min-h-44 cursor-pointer flex-col items-center justify-center rounded-[28px] border border-dashed border-stone-300 bg-stone-50/70 px-5 text-center transition hover:border-stone-400 hover:bg-white"
          >
            <input
              type="file"
              accept=".csv,.xlsx"
              className="hidden"
              onChange={handleChooseImportFile}
            />
            <Upload className="h-6 w-6 text-stone-400" />
            <div className="mt-3 text-sm font-medium text-stone-800">
              {importFile
                ? importFile.name
                : "拖拽 csv/xlsx 到这里，或点击选择文件"}
            </div>
            <div className="mt-2 text-xs text-stone-500">
              {importFile
                ? `已选 ${Math.round(importFile.size / 1024)} KB`
                : "支持 UTF-8 CSV 和 Excel 文件"}
            </div>
          </label>

          {importResult ? (
            <div className="mt-4 rounded-3xl border border-emerald-200 bg-emerald-50 px-4 py-4 text-sm text-emerald-800">
              <div className="font-medium">{importResult.message}</div>
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                <span className="rounded-full bg-white/80 px-3 py-1">
                  新增 {importResult.inserted_count}
                </span>
                <span className="rounded-full bg-white/80 px-3 py-1">
                  更新 {importResult.updated_count}
                </span>
                <span className="rounded-full bg-white/80 px-3 py-1">
                  失败 {importResult.failed_count}
                </span>
              </div>
            </div>
          ) : null}

          <div className="mt-5 flex flex-wrap justify-end gap-3">
            <button
              type="button"
              onClick={() => {
                setImportModalOpen(false);
                setImportResult(null);
                setImportFile(null);
              }}
              className="ui-btn-secondary"
            >
              关闭
            </button>
            <button
              type="button"
              onClick={() => void handleImportSubmit()}
              disabled={importingFile}
              className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
            >
              {importingFile ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : null}
              开始导入
            </button>
          </div>
        </div>
      </div>
    </ModalShell>
  );
}
