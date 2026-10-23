import type { Node } from "@tiptap/pm/model";
import { marked } from "marked";
import cjkFriendly from "marked-cjk-friendly";

// Letters are Markdown with only the formats docs/design.md#邮件格式 allows. This is the app's one
// Markdown parser, and the editor's serializer that writes what it reads back the same.

// Markdown for anything else, such as a line starting with "#" or a bare address, stays text: the
// editor could not hold it, and the letter would lose it.
const off = () => undefined;
marked.use(cjkFriendly(), {
  // A line break in a letter is meant as one.
  breaks: true,
  tokenizer: {
    heading: off,
    lheading: off,
    hr: off,
    blockquote: off,
    code: off,
    fences: off,
    html: off,
    def: off,
    table: off,
    tag: off,
    codespan: off,
    del: off,
    url: off,
    autolink: off,
  },
});

/** A letter's Markdown as HTML. */
export function toHtml(markdown: string): string {
  return marked.parse(markdown, { async: false });
}

/** An editor document as Markdown. */
export function toMarkdown(doc: Node): string {
  // Markdown has no empty paragraphs, so blank lines typed between paragraphs are not kept.
  return blocksOf(doc)
    .filter((b) => b !== "")
    .join("\n\n");
}

function blocksOf(parent: Node): string[] {
  const out: string[] = [];
  parent.forEach((node) => out.push(block(node)));
  return out;
}

function block(node: Node): string {
  switch (node.type.name) {
    case "paragraph":
      return inline(node);
    case "bulletList":
      return items(node, () => "- ");
    case "orderedList":
      return items(node, (i) => `${Number(node.attrs.start) + i}. `);
    default:
      throw new Error(`letters have no ${node.type.name}`);
  }
}

function items(list: Node, marker: (index: number) => string): string {
  const out: string[] = [];
  list.forEach((item, _, index) => {
    const m = marker(index);
    // A nested list right under its item's text keeps the list tight.
    const body = blocksOf(item).reduce((text, b, i) => {
      const sep = item.child(i).type.name === "paragraph" ? "\n\n" : "\n";
      return i === 0 ? b : text + sep + b;
    }, "");
    out.push(m + body.replace(/\n(?=.)/g, `\n${" ".repeat(m.length)}`));
  });
  return out.join("\n");
}

type Mark = { name: string; href?: string };
type Open = Mark & { close: string };

const sameMark = (a: Mark, b: Mark) => a.name === b.name && a.href === b.href;

function marksOf(node: Node | undefined): Mark[] {
  return (node?.marks ?? []).map((m) => ({
    name: m.type.name,
    href: m.type.name === "link" ? String(m.attrs.href) : undefined,
  }));
}

/**
 * A paragraph's text and marks. Marks that cross are closed and reopened; a reopening right after a
 * closing uses underscores, as `***` followed by `*` would read differently.
 */
function inline(para: Node): string {
  const children: Node[] = [];
  para.forEach((child) => children.push(child));
  // How many children in a row from `index` carry the mark, so the longest is opened outermost.
  const extent = (mark: Mark, index: number) => {
    let i = index;
    while (i < children.length && marksOf(children[i]).some((m) => sameMark(m, mark))) i++;
    return i - index;
  };
  let out = "";
  let open: Open[] = [];
  // Spaces at the end of marked text, written after its closing: delimiters next to spaces would
  // not count as emphasis.
  let space = "";
  children.forEach((child, index) => {
    if (child.type.name === "hardBreak") {
      // A break at the end shows nothing. A backslash, unlike two spaces, keeps two in a row from
      // reading as a new paragraph.
      if (children.slice(index + 1).some((c) => c.type.name !== "hardBreak")) out += `${space}\\\n`;
      space = "";
      return;
    }
    const marks = marksOf(child);
    // Marks stay open up to the first that this text does not have.
    let keep = open.findIndex((o) => !marks.some((m) => sameMark(m, o)));
    if (keep === -1) keep = open.length;
    const closing = open.slice(keep).toReversed();
    out += closing.map((o) => o.close).join("") + space;
    open = open.slice(0, keep);
    const text = child.text ?? "";
    const middle = text.trim();
    if (middle === "") {
      out += text;
      space = "";
      return;
    }
    const opening = marks
      .filter((m) => !open.some((o) => sameMark(o, m)))
      .toSorted((a, b) => extent(b, index) - extent(a, index));
    out += opening.length > 0 ? text.slice(0, text.indexOf(middle)) : "";
    for (const mark of opening) {
      const o: Open = { ...mark, close: closer(mark, closing.length > 0) };
      out += mark.name === "link" ? "[" : o.close;
      open.push(o);
    }
    const next = marksOf(children[index + 1]);
    const changes = next.length !== marks.length || marks.some((m) => !next.some((n) => sameMark(m, n)));
    const body = opening.length > 0 ? text.slice(text.indexOf(middle)) : text;
    space = changes && marks.length > 0 ? body.slice(body.trimEnd().length) : "";
    out += escape(space ? body.trimEnd() : body, out === "" || out.endsWith("\n"));
  });
  return (
    out +
    open
      .toReversed()
      .map((o) => o.close)
      .join("") +
    space
  );
}

function closer(mark: Mark, afterClosing: boolean): string {
  switch (mark.name) {
    case "bold":
      return afterClosing ? "__" : "**";
    case "italic":
      return afterClosing ? "_" : "*";
    case "link":
      // Brackets and spaces in an address would end the link early.
      return `](${(mark.href ?? "").replace(/[()<> ]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`)})`;
    default:
      throw new Error(`letters have no ${mark.name}`);
  }
}

/** Text as Markdown that reads back as the same text. */
function escape(text: string, lineStart: boolean): string {
  const escaped = text.replace(/[\\*_[\]&<>`~]/g, "\\$&");
  // Text that looks like a list item would start a list.
  return lineStart
    ? escaped.replace(
        /^(\s*)([-+]|\d+[.)])(?=\s|$)/,
        (_, s: string, m: string) => `${s}${m.slice(0, -1)}\\${m.slice(-1)}`,
      )
    : escaped;
}
