import type { ProfessorTagDTO } from "@/types";

type ProfessorTagChipsProps = {
  tags: ProfessorTagDTO[];
  maxVisible?: number;
  className?: string;
};

export const ProfessorTagChips = ({
  tags,
  maxVisible = 3,
  className = "",
}: ProfessorTagChipsProps) => {
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
  const hiddenLabel = tags
    .slice(maxVisible)
    .map((tag) => tag.name)
    .join("、");

  return (
    <div className={`flex min-w-0 flex-wrap items-center gap-1.5 ${className}`}>
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
        <span
          className="inline-flex items-center rounded-full border border-stone-200 bg-white px-2.5 py-1 text-xs font-medium text-stone-500"
          title={hiddenLabel}
        >
          +{hiddenCount}
        </span>
      ) : null}
    </div>
  );
};
