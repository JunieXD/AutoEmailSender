import { type CrawlerJobFormState } from "@/features/professor-management/model/professorManagementPage";
import type { CrawlJobEntryTypeDTO } from "@/types";
import { Loader2, Minus, Plus } from "lucide-react";
import type { Dispatch, RefObject, SetStateAction } from "react";
import {
  type ClipboardEvent as ReactClipboardEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { FieldLabel, ModalShell, inputClassName } from "./formControls";

type Props = {
  crawlerModalOpen: boolean;
  closeCrawlerModal: () => void;
  crawlerFormState: CrawlerJobFormState;
  setCrawlerFormState: Dispatch<SetStateAction<CrawlerJobFormState>>;
  crawlerUrlInputRefs: RefObject<(HTMLInputElement | null)[]>;
  handleCrawlerUrlKeyDown: (
    event: ReactKeyboardEvent<HTMLInputElement>,
    index: number,
  ) => void;
  handleCrawlerUrlPaste: (
    event: ReactClipboardEvent<HTMLInputElement>,
    index: number,
  ) => void;
  handleCreateCrawlJob: () => Promise<void>;
  crawlerSubmitDisabled: boolean;
  creatingCrawlJob: boolean;
};

export function CreateCrawlJobDialog({
  crawlerModalOpen,
  closeCrawlerModal,
  crawlerFormState,
  setCrawlerFormState,
  crawlerUrlInputRefs,
  handleCrawlerUrlKeyDown,
  handleCrawlerUrlPaste,
  handleCreateCrawlJob,
  crawlerSubmitDisabled,
  creatingCrawlJob,
}: Props) {
  return (
    <ModalShell
      open={crawlerModalOpen}
      title="创建抓取任务"
      description="填写学校、学院和页面 URL；结果进入候选审核。"
      onClose={closeCrawlerModal}
      maxWidthClassName="max-w-2xl"
    >
      <div className="mt-6 grid gap-4">
        <label className="block">
          {<FieldLabel label={"学校"} required={true} />}
          <input
            aria-label="学校"
            value={crawlerFormState.university}
            onChange={(event) =>
              setCrawlerFormState((previous) => ({
                ...previous,
                university: event.target.value,
              }))
            }
            className={inputClassName}
            placeholder="示例：示例大学"
          />
        </label>
        <label className="block">
          {<FieldLabel label={"学院"} required={true} />}
          <input
            aria-label="学院"
            value={crawlerFormState.school}
            onChange={(event) =>
              setCrawlerFormState((previous) => ({
                ...previous,
                school: event.target.value,
              }))
            }
            className={inputClassName}
            placeholder="示例：计算机学院"
          />
        </label>
        <fieldset className="grid gap-2">
          <legend className="text-sm font-medium text-stone-800">
            入口类型
          </legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {(
              [
                {
                  value: "list",
                  label: "列表页",
                  hint: "学院师资列表页",
                },
                {
                  value: "profile",
                  label: "详情页",
                  hint: "导师个人主页",
                },
              ] satisfies Array<{
                value: CrawlJobEntryTypeDTO;
                label: string;
                hint: string;
              }>
            ).map((option) => (
              <label
                key={option.value}
                className="flex cursor-pointer items-start gap-2 rounded-2xl border border-stone-200 bg-white px-3 py-2.5 text-sm text-stone-700 transition hover:border-primary/50"
              >
                <input
                  type="radio"
                  name="crawler-entry-type"
                  aria-label={option.label}
                  value={option.value}
                  checked={crawlerFormState.entry_type === option.value}
                  onChange={() =>
                    setCrawlerFormState((previous) => ({
                      ...previous,
                      entry_type: option.value,
                    }))
                  }
                  className="mt-1"
                />
                <span>
                  <span className="block font-medium text-stone-900">
                    {option.label}
                  </span>
                  <span className="block text-xs leading-5 text-stone-500">
                    {option.hint}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="grid gap-2">
          <div className="flex items-center justify-between gap-3">
            {<FieldLabel label={"页面 URL"} required={true} />}
            <button
              type="button"
              aria-label="添加页面 URL"
              onClick={() =>
                setCrawlerFormState((previous) => ({
                  ...previous,
                  start_urls: [...previous.start_urls, ""],
                }))
              }
              className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-600 transition hover:border-primary/50 hover:text-primary"
            >
              <Plus className="h-4 w-4" />
            </button>
          </div>
          <p id="crawler-url-hint" className="text-xs leading-5 text-stone-500">
            可一次粘贴多个 URL，每行一个，系统会自动拆分。
          </p>
          {crawlerFormState.start_urls.map((url, index) => (
            <div key={index} className="flex items-center gap-2">
              <input
                aria-label="页面 URL"
                aria-describedby="crawler-url-hint"
                ref={(element) => {
                  crawlerUrlInputRefs.current[index] = element;
                }}
                value={url}
                onChange={(event) => {
                  const nextValue = event.target.value;
                  setCrawlerFormState((previous) => ({
                    ...previous,
                    start_urls: previous.start_urls.map((item, itemIndex) =>
                      itemIndex === index ? nextValue : item,
                    ),
                  }));
                }}
                onKeyDown={(event) => handleCrawlerUrlKeyDown(event, index)}
                onPaste={(event) => handleCrawlerUrlPaste(event, index)}
                className={inputClassName}
                placeholder={
                  crawlerFormState.entry_type === "profile"
                    ? "示例：https://example.edu/faculty/zhang"
                    : "示例：https://example.edu/faculty"
                }
              />
              <button
                type="button"
                aria-label="移除页面 URL"
                onClick={() =>
                  setCrawlerFormState((previous) => ({
                    ...previous,
                    start_urls:
                      previous.start_urls.length > 1
                        ? previous.start_urls.filter(
                            (_, itemIndex) => itemIndex !== index,
                          )
                        : [""],
                  }))
                }
                disabled={crawlerFormState.start_urls.length === 1}
                className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-red-200 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-45"
              >
                <Minus className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      </div>
      <div className="mt-6 flex flex-wrap justify-end gap-3">
        <button
          type="button"
          onClick={closeCrawlerModal}
          className="ui-btn-secondary"
        >
          取消
        </button>
        <button
          type="button"
          onClick={() => void handleCreateCrawlJob()}
          disabled={crawlerSubmitDisabled}
          className="ui-btn-primary disabled:cursor-not-allowed disabled:opacity-60"
        >
          {creatingCrawlJob ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : null}
          开始抓取
        </button>
      </div>
    </ModalShell>
  );
}
