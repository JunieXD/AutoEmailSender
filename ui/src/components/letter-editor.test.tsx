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

it.each([
  ["我有**3 篇论文**", ["我有3 篇论文"]],
  ["见[主页](https://example.edu)", ["见主页"]],
  ["曾获奖。\n\n发表过论文", ["曾获奖。", "发表过论文"]],
])("marks a quote of the Markdown source as the editor shows it: %s", async (quote, marked) => {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(async () =>
      root.render(
        <LetterEditor
          id="letter"
          markdown={"王老师：我有**3 篇论文**，见[主页](https://example.edu)。\n\n曾获奖。\n\n发表过论文。"}
          placeholders={[]}
          quotes={[quote]}
          disabled={false}
          onChange={() => {}}
        />,
      ),
    );
    const marks = Array.from(container.querySelectorAll(".tiptap .bg-warning-soft"), (node) => node.textContent);
    // A mark is cut into several elements where formatting starts or ends.
    expect(marks.join("")).toBe(marked.join(""));
  } finally {
    await act(async () => root.unmount());
    container.remove();
  }
});
