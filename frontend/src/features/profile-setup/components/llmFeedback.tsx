import { PROFILE_HELP_LINKS } from "@/lib/helpLinks";
import {
  type LLMProfileModelsResultDTO,
  type LLMProfileTestResultDTO,
} from "@/types";
import clsx from "clsx";
import { useDeferredValue, useState } from "react";
import { ContextualHelpLink } from "./formControls";

const formatDuration = (durationMs: number | null) =>
  durationMs === null ? "未返回" : `${durationMs} ms`;

export const LlmModelsFeedbackPanel = ({
  result,
  currentModelName,
  onSelectModel,
}: {
  result: LLMProfileModelsResultDTO | null;
  currentModelName: string;
  onSelectModel: (modelName: string) => void;
}) => {
  const [searchKeyword, setSearchKeyword] = useState("");
  const deferredSearchKeyword = useDeferredValue(searchKeyword);

  if (!result) {
    return null;
  }

  const normalizedKeyword = deferredSearchKeyword.trim().toLowerCase();
  const filteredModels = result.models.filter((model) =>
    normalizedKeyword ? model.toLowerCase().includes(normalizedKeyword) : true,
  );
  const hasExactCurrentModel = result.models.includes(currentModelName.trim());

  return (
    <div
      className={clsx(
        "rounded-3xl border px-4 py-4 shadow-sm",
        result.ok
          ? "border-emerald-200 bg-emerald-50/80"
          : "border-red-200 bg-red-50/80",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-stone-900">基础连通性</span>
        <span
          className={clsx(
            "rounded-full px-2.5 py-1 text-[11px] font-medium",
            result.consumes_tokens
              ? "bg-amber-100 text-amber-700"
              : "bg-stone-900 text-white",
          )}
        >
          {result.consumes_tokens ? "会耗 Token" : "不耗 Token"}
        </span>
      </div>
      <p className="mt-2 text-sm leading-6 text-stone-700">{result.message}</p>
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-stone-600">
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          状态码：{result.status_code ?? "未返回"}
        </span>
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          耗时：{formatDuration(result.duration_ms)}
        </span>
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          端点：{result.endpoint_kind ?? "未识别"}
        </span>
      </div>
      {result.request_url ? (
        <div className="mt-3 rounded-2xl border border-stone-200 bg-white/90 px-3 py-2 text-xs leading-5 text-stone-600">
          <div className="font-medium text-stone-800">请求 URL</div>
          <div className="mt-1 break-all">{result.request_url}</div>
        </div>
      ) : null}
      {!result.ok ? (
        <div className="mt-3 border-t border-red-200/80 pt-3">
          <ContextualHelpLink
            href={PROFILE_HELP_LINKS.llmConfiguration}
            tone="surface"
          >
            查看模型配置排查步骤
          </ContextualHelpLink>
        </div>
      ) : null}
      {result.models.length > 0 ? (
        <div className="mt-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs font-medium text-stone-700">可用模型</div>
            </div>
            <span className="rounded-full border border-stone-200 bg-white px-2.5 py-1 text-[11px] text-stone-500">
              {filteredModels.length}/{result.models.length}
            </span>
          </div>
          <div className="mt-3 rounded-[24px] border border-stone-200 bg-white/90 p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.85)]">
            <input
              value={searchKeyword}
              onChange={(event) => setSearchKeyword(event.target.value)}
              className="w-full rounded-2xl border border-stone-200 bg-stone-50/80 px-3 py-2 text-sm text-stone-700 outline-none transition placeholder:text-stone-400 focus:border-primary focus:bg-white focus:ring-2 focus:ring-primary/15"
              placeholder="搜索模型名，点击进行选择"
            />
            {currentModelName.trim() ? (
              <div className="mt-3 rounded-2xl border border-stone-200 bg-stone-50/85 px-3 py-3">
                <div className="text-[11px] uppercase tracking-[0.18em] text-stone-400">
                  当前选择
                </div>
                <div className="mt-2 break-all text-sm font-medium leading-6 text-stone-800">
                  {currentModelName}
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-stone-500">
                  {hasExactCurrentModel ? (
                    <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-emerald-700">
                      已在列表中
                    </span>
                  ) : (
                    <span className="rounded-full bg-amber-100 px-2.5 py-1 text-amber-700">
                      不在当前列表中
                    </span>
                  )}
                </div>
              </div>
            ) : null}
            <div className="mt-3 max-h-56 overflow-y-auto pr-1">
              {filteredModels.length > 0 ? (
                <div className="space-y-2">
                  {filteredModels.map((model) => {
                    const active = model === currentModelName.trim();
                    return (
                      <button
                        key={model}
                        type="button"
                        onClick={() => onSelectModel(model)}
                        className={clsx(
                          "group flex w-full justify-between items-center gap-3 rounded-2xl border px-3 py-2 text-left transition",
                          active
                            ? "border-primary/20 bg-primary text-white shadow-sm shadow-primary/20"
                            : "border-stone-200 bg-stone-50/75 text-stone-700 hover:border-stone-300 hover:bg-white hover:text-stone-900",
                        )}
                      >
                        <div className="min-w-0 flex-1">
                          <div className="break-all text-sm font-medium leading-5">
                            {model}
                          </div>
                        </div>
                        <div
                          className={clsx(
                            "mt-0.5 shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium",
                            active
                              ? "bg-white/18 text-white"
                              : "bg-stone-100 text-stone-500 group-hover:bg-stone-200 group-hover:text-stone-700",
                          )}
                        >
                          {active ? "当前" : "选择"}
                        </div>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="rounded-2xl border border-dashed border-stone-200 bg-stone-50/70 px-4 py-6 text-center text-xs text-stone-500">
                  没找到匹配的模型名，试试换个关键词。
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export const LlmTestFeedbackPanel = ({
  result,
}: {
  result: LLMProfileTestResultDTO | null;
}) => {
  if (!result) {
    return null;
  }

  return (
    <div
      className={clsx(
        "rounded-3xl border px-4 py-4 shadow-sm",
        result.ok
          ? "border-emerald-200 bg-emerald-50/80"
          : "border-red-200 bg-red-50/80",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-stone-900">测试模型</span>
        <span
          className={clsx(
            "rounded-full px-2.5 py-1 text-[11px] font-medium",
            result.consumes_tokens
              ? "bg-amber-100 text-amber-700"
              : "bg-stone-900 text-white",
          )}
        >
          {result.consumes_tokens ? "会耗 Token" : "不耗 Token"}
        </span>
      </div>
      <p className="mt-2 text-sm leading-6 text-stone-700">{result.message}</p>
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-stone-600">
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          状态码：{result.status_code ?? "未返回"}
        </span>
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          耗时：{formatDuration(result.duration_ms)}
        </span>
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          端点：{result.endpoint_kind ?? "未识别"}
        </span>
      </div>
      {result.request_url ? (
        <div className="mt-3 rounded-2xl border border-stone-200 bg-white/90 px-3 py-2 text-xs leading-5 text-stone-600">
          <div className="font-medium text-stone-800">最终请求 URL</div>
          <div className="mt-1 break-all">{result.request_url}</div>
        </div>
      ) : null}
      {result.attempted_urls.length > 1 ? (
        <div className="mt-3 rounded-2xl border border-stone-200 bg-white/90 px-3 py-2 text-xs leading-5 text-stone-600">
          <div className="font-medium text-stone-800">尝试过的 URL</div>
          <div className="mt-1 break-all">
            {result.attempted_urls.join("\n")}
          </div>
        </div>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-stone-600">
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          输入 Token：{result.prompt_tokens ?? "未返回"}
        </span>
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          输出 Token：{result.completion_tokens ?? "未返回"}
        </span>
        <span className="rounded-full border border-stone-200 bg-white px-3 py-1">
          总 Token：{result.total_tokens ?? "未返回"}
        </span>
      </div>
      {result.response_preview ? (
        <div className="mt-3 rounded-2xl border border-stone-200 bg-white/90 px-3 py-2 text-xs leading-5 text-stone-600">
          <div className="font-medium text-stone-800">响应预览</div>
          <div className="mt-1 whitespace-pre-wrap">
            {result.response_preview}
          </div>
        </div>
      ) : null}
      {!result.ok ? (
        <div className="mt-3 border-t border-red-200/80 pt-3">
          <ContextualHelpLink
            href={PROFILE_HELP_LINKS.llmConfiguration}
            tone="surface"
          >
            查看模型配置排查步骤
          </ContextualHelpLink>
        </div>
      ) : null}
    </div>
  );
};
