import { useEffect, useRef, useState } from "react";
import { Plus } from "lucide-react";
import type { ProfessorTagDTO } from "@/types";

type ProfessorTagChipsProps = {
  tags?: ProfessorTagDTO[];
  maxVisible?: number;
  className?: string;
  onTagClick?: (tagId: number) => void;
  onAddTag?: () => void;
  draggableTags?: boolean;
  onTagOrderChange?: (tagIds: number[]) => void;
};

export const ProfessorTagChips = ({
  tags,
  maxVisible = 3,
  className = "",
  onTagClick,
  onAddTag,
  draggableTags = false,
  onTagOrderChange,
}: ProfessorTagChipsProps) => {
  const safeTags = tags ?? [];
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [popoverPinned, setPopoverPinned] = useState(false);
  const [draggingTagId, setDraggingTagId] = useState<number | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const suppressClickTagIdRef = useRef<number | null>(null);

  useEffect(() => {
    if (!popoverPinned) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (rootRef.current?.contains(event.target as Node)) {
        return;
      }
      setPopoverPinned(false);
      setPopoverOpen(false);
    };

    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [popoverPinned]);

  if (safeTags.length === 0) {
    return (
      <div
        data-testid="professor-tag-chips"
        className={`flex min-w-0 flex-wrap items-center gap-1.5 ${className}`}
      >
        {onAddTag ? (
          <button
            type="button"
            aria-label="给导师添加标签"
            onClick={onAddTag}
            className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-primary/40 hover:text-primary"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
    );
  }

  const visibleTags = safeTags.slice(0, maxVisible);
  const hiddenTags = safeTags.slice(visibleTags.length);
  const hiddenCount = hiddenTags.length;

  const moveDraggedTagBefore = (targetTagId: number) => {
    if (!onTagOrderChange || draggingTagId === null || draggingTagId === targetTagId) {
      return;
    }
    const nextTagIds = safeTags
      .map((tag) => tag.id)
      .filter((tagId) => tagId !== draggingTagId);
    const targetIndex = nextTagIds.indexOf(targetTagId);
    if (targetIndex < 0) {
      return;
    }
    nextTagIds.splice(targetIndex, 0, draggingTagId);
    onTagOrderChange(nextTagIds);
  };

  const markDragDropComplete = () => {
    suppressClickTagIdRef.current = draggingTagId;
    setDraggingTagId(null);
  };

  return (
    <div
      ref={rootRef}
      data-testid="professor-tag-chips"
      className={`relative flex min-w-0 flex-wrap items-center gap-1.5 ${className}`}
      onMouseLeave={() => {
        if (!popoverPinned) {
          setPopoverOpen(false);
        }
      }}
    >
      {visibleTags.map((tag) => (
        <span
          key={tag.id}
          draggable={draggableTags}
          onDragStart={() => setDraggingTagId(tag.id)}
          onDragOver={(event) => event.preventDefault()}
          onDrop={() => {
            moveDraggedTagBefore(tag.id);
            markDragDropComplete();
          }}
          onDragEnd={() => setDraggingTagId(null)}
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
          onClick={() => {
            setPopoverPinned((previous) => {
              const nextPinned = !previous;
              setPopoverOpen(nextPinned || !popoverOpen);
              return nextPinned;
            });
          }}
          onMouseEnter={() => {
            setPopoverOpen(true);
          }}
          className="inline-flex items-center rounded-full border border-stone-200 bg-white px-2.5 py-1 text-xs font-medium text-stone-500 transition hover:border-stone-300 hover:bg-stone-50 hover:text-stone-700"
        >
          +{hiddenCount}
        </button>
      ) : null}
      {onAddTag ? (
        <button
          type="button"
          aria-label="给导师添加标签"
          onClick={onAddTag}
          className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-stone-200 bg-white text-stone-500 transition hover:border-primary/40 hover:text-primary"
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      ) : null}
      {popoverOpen ? (
        <div
          role="dialog"
          aria-label="折叠标签"
          className="absolute left-0 top-[calc(100%+0.35rem)] z-50 min-w-44 rounded-2xl border border-stone-200 bg-white p-2 shadow-[0_18px_42px_-24px_rgba(41,37,36,0.45)]"
        >
          <div className="flex max-w-72 flex-wrap gap-1.5">
            {hiddenTags.map((tag) =>
              onTagClick ? (
                <button
                  key={tag.id}
                  type="button"
                  aria-label={`选择标签 ${tag.name}`}
                  draggable={draggableTags}
                  onDragStart={() => setDraggingTagId(tag.id)}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={() => {
                    moveDraggedTagBefore(tag.id);
                    markDragDropComplete();
                  }}
                  onDragEnd={() => setDraggingTagId(null)}
                  onClick={() => {
                    if (suppressClickTagIdRef.current === tag.id) {
                      suppressClickTagIdRef.current = null;
                      return;
                    }
                    suppressClickTagIdRef.current = null;
                    onTagClick(tag.id);
                  }}
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
                  draggable={draggableTags}
                  onDragStart={() => setDraggingTagId(tag.id)}
                  onDragOver={(event) => event.preventDefault()}
                  onDrop={() => {
                    moveDraggedTagBefore(tag.id);
                    markDragDropComplete();
                  }}
                  onDragEnd={() => setDraggingTagId(null)}
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
