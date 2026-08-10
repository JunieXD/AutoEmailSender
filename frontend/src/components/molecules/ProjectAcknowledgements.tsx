import { ExternalLink, HeartHandshake } from "lucide-react";
import { openExternalHttpUrl } from "@/lib/externalUrls";

const ACKNOWLEDGEMENTS_URL =
  "https://juniexd.github.io/AutoEmailSender/acknowledgements";
const SUPPORTER_NAMES = ["羽华丶"];
const SUPPORTER_PREVIEW_LIMIT = 6;

export function ProjectAcknowledgements() {
  const visibleSupporters = SUPPORTER_NAMES.slice(0, SUPPORTER_PREVIEW_LIMIT);
  const hiddenSupporters = SUPPORTER_NAMES.slice(SUPPORTER_PREVIEW_LIMIT);

  return (
    <section
      aria-labelledby="project-acknowledgements-title"
      className="flex flex-col gap-4 border-t border-stone-200 px-1 pt-6 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex min-w-0 items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
          <HeartHandshake className="h-5 w-5" aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <h2
            id="project-acknowledgements-title"
            className="text-sm font-semibold text-stone-900"
          >
            致谢
          </h2>
          <p className="mt-1 text-sm leading-6 text-stone-600">
            感谢所有提供模型额度和开发支持的贡献者。
          </p>
          <div
            className="mt-2 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs leading-5"
            aria-label={`支持名单：${SUPPORTER_NAMES.join("、")}`}
          >
            <span className="text-stone-500">支持名单</span>
            <span className="font-medium text-stone-800">
              {visibleSupporters.join("、")}
            </span>
          </div>
          {hiddenSupporters.length > 0 ? (
            <details className="mt-1 text-xs leading-5 text-stone-600">
              <summary className="w-fit cursor-pointer font-medium text-stone-700 hover:text-stone-900">
                另有 {hiddenSupporters.length} 位同学
              </summary>
              <p className="mt-1 text-stone-700">{hiddenSupporters.join("、")}</p>
            </details>
          ) : null}
        </div>
      </div>

      <button
        type="button"
        onClick={() => openExternalHttpUrl(ACKNOWLEDGEMENTS_URL)}
        className="inline-flex shrink-0 items-center justify-center gap-2 self-start rounded-xl border border-stone-200 bg-white px-3.5 py-2 text-sm font-medium whitespace-nowrap text-stone-700 transition hover:border-stone-300 hover:bg-stone-50 active:translate-y-px focus:outline-none focus:ring-2 focus:ring-primary/20 sm:self-center"
        aria-label="查看完整致谢，在浏览器中打开"
      >
        查看完整致谢
        <ExternalLink className="h-4 w-4" aria-hidden="true" />
      </button>
    </section>
  );
}
