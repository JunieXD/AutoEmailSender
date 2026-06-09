import type { ProfessorBulkTagModeDTO } from "@/types";

export const bulkTagConfirmLabels: Record<
  ProfessorBulkTagModeDTO,
  { title: string; confirmLabel: string }
> = {
  add: {
    title: "确认追加标签？",
    confirmLabel: "确认追加",
  },
  remove: {
    title: "确认移除标签？",
    confirmLabel: "确认移除",
  },
  replace: {
    title: "确认覆盖标签？",
    confirmLabel: "确认覆盖",
  },
};

export const buildBulkTagConfirmDescription = ({
  mode,
  selectedCount,
  tagNames,
}: {
  mode: ProfessorBulkTagModeDTO;
  selectedCount: number;
  tagNames: string[];
}) => {
  const tagDescription =
    tagNames.length > 0 ? tagNames.join("、") : "不选择任何标签";

  if (mode === "replace" && tagNames.length === 0) {
    return `将清空选中的 ${selectedCount} 位导师的全部标签。原来的标签将会被替换。`;
  }

  if (mode === "replace") {
    return `将“${tagDescription}”覆盖选中的 ${selectedCount} 位导师，原来的标签将会被替换。`;
  }

  if (mode === "remove") {
    return `将“${tagDescription}”移除选中的 ${selectedCount} 位导师。`;
  }

  return `将“${tagDescription}”追加到选中的 ${selectedCount} 位导师。`;
};
