import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react";
import { PageSizeSelector } from "@/components/molecules/PageSizeSelector";
import {
  clampPageSize,
  getPageForPageSizeChange,
  getPaginationItems,
  getTotalPages,
  PAGE_SIZE_OPTIONS,
  type PaginationChange,
} from "@/lib/pagination";

type PaginationProps = {
  page: number;
  pageSize: number;
  totalCount: number;
  onChange: (change: PaginationChange) => void;
  ariaLabel: string;
  variant?: "standard" | "compact";
  pageSizeOptions?: readonly number[];
  unitLabel?: string;
  itemLabel?: string;
  summary?: ReactNode;
  focusTargetRef?: RefObject<HTMLElement | null>;
  disabled?: boolean;
  className?: string;
  menuPlacement?: "popover" | "inline" | "floating-up";
  pageSizeAriaLabel?: string;
  pageStatusPrefix?: string;
};

type PendingFocus = {
  page: number;
  pageSize: number;
};

const iconButtonClassName =
  "inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-600 transition hover:border-primary/30 hover:bg-primary/5 hover:text-primary disabled:cursor-not-allowed disabled:opacity-40";

const pageButtonClassName =
  "inline-flex h-9 min-w-9 items-center justify-center rounded-full border px-2 text-sm font-medium tabular-nums transition";

export const Pagination = ({
  page,
  pageSize,
  totalCount,
  onChange,
  ariaLabel,
  variant = "standard",
  pageSizeOptions = PAGE_SIZE_OPTIONS,
  unitLabel = "条",
  itemLabel = "条",
  summary,
  focusTargetRef,
  disabled = false,
  className,
  menuPlacement,
  pageSizeAriaLabel = "每页数量",
  pageStatusPrefix = "",
}: PaginationProps) => {
  const safePageSize = clampPageSize(pageSize);
  const safeTotalCount = Number.isFinite(totalCount)
    ? Math.max(0, Math.trunc(totalCount))
    : 0;
  const totalPages = getTotalPages(safeTotalCount, safePageSize);
  const safePage = Number.isFinite(page)
    ? Math.min(totalPages, Math.max(1, Math.trunc(page)))
    : 1;
  const pageItems = useMemo(
    () => getPaginationItems(safePage, totalPages),
    [safePage, totalPages],
  );
  const [jumpValue, setJumpValue] = useState(String(safePage));
  const [jumpError, setJumpError] = useState<string | null>(null);
  const jumpErrorId = useId();
  const pendingFocusRef = useRef<PendingFocus | null>(null);
  const previousDisabledRef = useRef(disabled);

  useEffect(() => {
    setJumpValue(String(safePage));
    setJumpError(null);
  }, [safePage, totalPages]);

  useEffect(() => {
    const pendingFocus = pendingFocusRef.current;
    if (
      !pendingFocus ||
      pendingFocus.page !== safePage ||
      pendingFocus.pageSize !== safePageSize
    ) {
      return;
    }

    pendingFocusRef.current = null;
    const target = focusTargetRef?.current;
    if (!target) {
      return;
    }

    try {
      target.focus({ preventScroll: true });
    } catch {
      target.focus();
    }
    target.scrollIntoView?.({ behavior: "auto", block: "start" });
  }, [focusTargetRef, safePage, safePageSize]);

  useEffect(() => {
    const wasDisabled = previousDisabledRef.current;
    previousDisabledRef.current = disabled;
    if (!wasDisabled || disabled) {
      return;
    }

    const pendingFocus = pendingFocusRef.current;
    if (
      pendingFocus &&
      (pendingFocus.page !== safePage ||
        pendingFocus.pageSize !== safePageSize)
    ) {
      pendingFocusRef.current = null;
    }
  }, [disabled, safePage, safePageSize]);

  if (safeTotalCount === 0) {
    return null;
  }

  const emitChange = (change: PaginationChange) => {
    if (
      change.page === safePage &&
      change.pageSize === safePageSize
    ) {
      return;
    }

    pendingFocusRef.current = {
      page: change.page,
      pageSize: change.pageSize,
    };
    try {
      onChange(change);
    } catch (error) {
      pendingFocusRef.current = null;
      throw error;
    }
  };

  const changePage = (nextPage: number) => {
    emitChange({
      page: Math.min(totalPages, Math.max(1, Math.trunc(nextPage))),
      pageSize: safePageSize,
      reason: "page",
    });
  };

  const changePageSize = (nextPageSize: number) => {
    const safeNextPageSize = clampPageSize(nextPageSize);
    emitChange({
      page: getPageForPageSizeChange({
        page: safePage,
        pageSize: safePageSize,
        nextPageSize: safeNextPageSize,
        totalCount: safeTotalCount,
      }),
      pageSize: safeNextPageSize,
      reason: "page-size",
    });
  };

  const commitJump = () => {
    const nextPage = Number(jumpValue.trim());
    if (
      !Number.isInteger(nextPage) ||
      nextPage < 1 ||
      nextPage > totalPages
    ) {
      setJumpError(`请输入 1–${totalPages} 之间的页码`);
      return;
    }

    setJumpError(null);
    changePage(nextPage);
  };

  const handleJumpKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commitJump();
    } else if (event.key === "Escape") {
      setJumpValue(String(safePage));
      setJumpError(null);
    }
  };

  const startItem = (safePage - 1) * safePageSize + 1;
  const endItem = Math.min(safeTotalCount, safePage * safePageSize);
  const resolvedMenuPlacement =
    menuPlacement ?? (variant === "compact" ? "popover" : "floating-up");
  const showQuickJump = variant === "standard" && totalPages > 7;

  return (
    <nav
      aria-label={ariaLabel}
      className={`flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between ${className ?? ""}`}
    >
      <div className="text-xs tabular-nums text-stone-500 sm:text-sm">
        {summary ?? `显示 ${startItem}-${endItem} / ${safeTotalCount} ${itemLabel}`}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <PageSizeSelector
          value={safePageSize}
          onChange={changePageSize}
          options={pageSizeOptions}
          unitLabel={unitLabel}
          menuPlacement={resolvedMenuPlacement}
          disabled={disabled}
          ariaLabel={pageSizeAriaLabel}
        />

        {totalPages > 1 ? (
          <div
            role="group"
            className="inline-flex items-center gap-1"
            aria-label="翻页按钮"
          >
            <button
              type="button"
              onClick={() => changePage(1)}
              disabled={disabled || safePage <= 1}
              className={iconButtonClassName}
              aria-label="首页"
              title="首页"
            >
              <ChevronsLeft className="h-4 w-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => changePage(safePage - 1)}
              disabled={disabled || safePage <= 1}
              className={iconButtonClassName}
              aria-label="上一页"
              title="上一页"
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            </button>

            {variant === "standard" ? (
              <div className="hidden items-center gap-1 sm:inline-flex">
                {pageItems.map((item) =>
                  typeof item === "number" ? (
                    <button
                      key={item}
                      type="button"
                      onClick={() => changePage(item)}
                      disabled={disabled}
                      aria-label={`第 ${item} 页`}
                      aria-current={item === safePage ? "page" : undefined}
                      className={`${pageButtonClassName} ${
                        item === safePage
                          ? "border-primary bg-primary text-white shadow-sm shadow-primary/20"
                          : "border-stone-200 bg-white text-stone-600 hover:border-primary/30 hover:bg-primary/5 hover:text-primary"
                      } disabled:cursor-not-allowed disabled:opacity-50`}
                    >
                      {item}
                    </button>
                  ) : (
                    <span
                      key={item}
                      aria-hidden="true"
                      className="inline-flex h-9 min-w-6 items-center justify-center text-stone-400"
                    >
                      …
                    </span>
                  ),
                )}
              </div>
            ) : null}

            <span
              className={
                variant === "standard"
                  ? "min-w-16 text-center text-xs font-medium tabular-nums text-stone-600 sm:hidden"
                  : "min-w-20 text-center text-xs font-medium tabular-nums text-stone-600"
              }
            >
              {pageStatusPrefix}
              {safePage} / {totalPages} 页
            </span>

            <button
              type="button"
              onClick={() => changePage(safePage + 1)}
              disabled={disabled || safePage >= totalPages}
              className={iconButtonClassName}
              aria-label="下一页"
              title="下一页"
            >
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => changePage(totalPages)}
              disabled={disabled || safePage >= totalPages}
              className={iconButtonClassName}
              aria-label="尾页"
              title="尾页"
            >
              <ChevronsRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        ) : null}

        {showQuickJump ? (
          <div className="flex flex-wrap items-center gap-2 text-xs text-stone-500">
            <span>前往</span>
            <input
              type="number"
              min={1}
              max={totalPages}
              step={1}
              value={jumpValue}
              disabled={disabled}
              aria-label="输入页码"
              aria-invalid={Boolean(jumpError)}
              aria-describedby={jumpError ? jumpErrorId : undefined}
              onChange={(event) => {
                setJumpValue(event.target.value);
                setJumpError(null);
              }}
              onKeyDown={handleJumpKeyDown}
              className="h-9 w-16 rounded-xl border border-stone-200 bg-white px-2 text-center text-sm tabular-nums text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15 disabled:cursor-not-allowed disabled:opacity-60"
            />
            <button
              type="button"
              onClick={commitJump}
              disabled={disabled}
              className="ui-btn-secondary h-9 px-3 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-60"
            >
              跳转
            </button>
            {jumpError ? (
              <span id={jumpErrorId} role="alert" className="text-red-600">
                {jumpError}
              </span>
            ) : null}
          </div>
        ) : null}
      </div>
    </nav>
  );
};
