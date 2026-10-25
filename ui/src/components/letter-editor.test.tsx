// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import { LetterEditor } from "./letter-editor";

it.each([
  ["【请**补充**：面谈时间】", "【请补充：面谈时间】"],
  ["【**请补充：**面谈时间】", "【请补充：面谈时间】"],
  ["【请[补充](https://example.edu)：面谈时间】", "【请补充：面谈时间】"],
  ["【请补充:面谈时间】", "【请补充:面谈时间】"],
  ["【请补\n充：面谈时间】", "【请补充：面谈时间】"],
])("highlights the whole visible marker across inline nodes: %s", async (markdown, visible) => {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () =>
      root.render(
        <LetterEditor
          id="letter"
          markdown={`王老师：【重点】时间为${markdown}。`}
          placeholders={[]}
          unfilled
          disabled={false}
          onChange={() => {}}
        />,
      ),
    );
    const highlighted = Array.from(container.querySelectorAll(".tiptap span"), (node) => node.textContent).join("");
    expect(highlighted).toBe(visible);
  } finally {
    await act(async () => root.unmount());
    container.remove();
  }
});
