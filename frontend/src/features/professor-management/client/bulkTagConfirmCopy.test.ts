import { describe, expect, it } from "vitest";
import {
  bulkTagConfirmLabels,
  buildBulkTagConfirmDescription,
} from "./bulkTagConfirmCopy";

describe("bulkTagConfirmCopy", () => {
  it("describes removing selected tags with the remove action", () => {
    expect(
      buildBulkTagConfirmDescription({
        mode: "remove",
        selectedCount: 3,
        tagNames: ["已联系", "高意愿"],
      }),
    ).toBe("将“已联系、高意愿”移除选中的 3 位导师。");
  });

  it("keeps add and replace confirmation labels available", () => {
    expect(bulkTagConfirmLabels.add.confirmLabel).toBe("确认追加");
    expect(bulkTagConfirmLabels.remove.confirmLabel).toBe("确认移除");
    expect(bulkTagConfirmLabels.replace.confirmLabel).toBe("确认覆盖");
  });

  it("describes empty replace as clearing all tags", () => {
    expect(
      buildBulkTagConfirmDescription({
        mode: "replace",
        selectedCount: 2,
        tagNames: [],
      }),
    ).toBe("将清空选中的 2 位导师的全部标签。原来的标签将会被替换。");
  });
});
