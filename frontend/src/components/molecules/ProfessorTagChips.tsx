import { useState } from "react";
import type { ProfessorTagDTO } from "@/types";

type ProfessorTagChipsProps = {
  tags: ProfessorTagDTO[];
  maxVisible?: number;
  className?: string;
  onTagClick?: (tagId: number) => void;
};

export const ProfessorTagChips = ({
  tags,
  maxVisible = 3,
  className = "",
  onTagClick,
}: ProfessorTagChipsProps) => {
  const [popoverOpen, setPopoverOpen] = useState(false);

  if (tags.length === 0) {
    return (
      <div className={`flex min-w-0 flex-wrap items-center gap-1.5 ${className}`}>
        <span className="inline-flex max-w-full items-center rounded-full border border-stone-200 bg-stone-50 px-2.5 py-1 text-xs font-medium text-stone-500">
          暂无标签
        </span>
      </div>
    );
  }

  const visibleTags = tags.slice(0, maxVisible);
  const hiddenCount = Math.max(0, tags.length - visibleTags.length);

  return (
    <div
      className={`relative flex min-w-0 flex-wrap items-center gap-1.5 ${className}`}
      onMouseLeave={() => setPopoverOpen(false)}
    >
      {visibleTags.map((tag) => (
        <span
          key={tag.id}
          className="inline-flex max-w-full items-center truncate rounded-full px-2.5 py-1 text-xs font-medium"
          style={{
            backgroundColor: tag.background_color,
            color: tag.text_color,
          }}
          title={tag.name}
        >
          {tag.name}
        </span>
      ))}
      {hiddenCount > 0 ? (
        <button
          type="button"
          aria-label={`查看全部标签，剩余 ${hiddenCount} 个`}
          onClick={() => setPopoverOpen((previous) => !previous)}
          onMouseEnter={() => setPopoverOpen(true)}
          className="inline-flex items-center rounded-full border border-stone-200 bg-white px-2.5 py-1 text-xs font-medium text-stone-500 transition hover:border-stone-300 hover:bg-stone-50 hover:text-stone-700"
        >
          +{hiddenCount}
        </button>
      ) : null}
      {popoverOpen ? (
        <div
          role="dialog"
          aria-label="全部标签"
          className="absolute left-0 top-[calc(100%+0.35rem)] z-50 min-w-44 rounded-2xl border border-stone-200 bg-white p-2 shadow-[0_18px_42px_-24px_rgba(41,37,36,0.45)]"
        >
          <div className="flex max-w-72 flex-wrap gap-1.5">
            {tags.map((tag) =>
              onTagClick ? (
                <button
                  key={tag.id}
                  type="button"
                  aria-label={`选择标签 ${tag.name}`}
                  onClick={() => onTagClick(tag.id)}
                  className="inline-flex max-w-full items-center truncate rounded-full px-2.5 py-1 text-xs font-medium transition hover:brightness-95"
                  style={{
                    backgroundColor: tag.background_color,
                    color: tag.text_color,
                  }}
                >
                  {tag.name}
                </button>
              ) : (
                <span
                  key={tag.id}
                  className="inline-flex max-w-full items-center truncate rounded-full px-2.5 py-1 text-xs font-medium"
                  style={{
                    backgroundColor: tag.background_color,
                    color: tag.text_color,
                  }}
                >
                  {tag.name}
                </span>
              ),
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
};
