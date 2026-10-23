import type { Node } from "@tiptap/pm/model";
import { marked } from "marked";
import cjkFriendly from "marked-cjk-friendly";

// Letters are Markdown with only the formats docs/design.md#邮件格式 allows. This is the app's one
// Markdown parser, and the editor's serializer that writes what it reads back the same.

/** Whether a letter may link to an address: web pages and email addresses, never scripts. */
export const linkable = (address: string) => /^(https?|mailto):/i.test(address);

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
  renderer: {
    // Markdown comes from the model too, so a link elsewhere is only its text.
    link(token) {
      return linkable(token.href) ? false : this.parser.parseInline(token.tokens);
    },
    // Pictures and task boxes stay as written: a letter has neither.
    image({ raw }) {
      return this.text({ type: "text", raw, text: raw });
    },
    checkbox({ raw }) {
      return this.text({ type: "text", raw, text: raw });
    },
    // An item whose own text was deleted still holds its nested list, after the empty paragraph the
    // editor needs first.
    listitem(item) {
      return item.tokens[0]?.type === "list" ? `<li><p></p>${this.parser.parse(item.tokens)}</li>\n` : false;
    },
  },
});

/** A letter's Markdown as HTML. */
export function toHtml(markdown: string): string {
  return marked.parse(markdown, { async: false });
}

/** An editor document as Markdown. */
export function toMarkdown(doc: Node): string {
  return blocksOf(doc)
    .map((b) => b.text)
    .join("\n\n");
}

type Block = { node: Node; text: string; otherMarker: boolean };

/** Markdown has no blank paragraphs, so blank lines typed between paragraphs are not kept. */
function blocksOf(parent: Node): Block[] {
  const out: Block[] = [];
  parent.forEach((node) => {
    const previous = out.at(-1);
    // Two lists of a kind in a row would read as one, unless their markers differ.
    const otherMarker = previous?.node.type === node.type && !previous.otherMarker;
    const text = block(node, otherMarker);
    if (!/^[ \t]*$/.test(text)) out.push({ node, text, otherMarker });
  });
  return out;
}

function block(node: Node, otherMarker: boolean): string {
  switch (node.type.name) {
    case "paragraph":
      return inline(node);
    case "bulletList":
      return items(node, () => (otherMarker ? "+ " : "- "));
    case "orderedList":
      return items(node, (i) => `${Number(node.attrs.start) + i}${otherMarker ? ")" : "."} `);
    default:
      throw new Error(`letters have no ${node.type.name}`);
  }
}

function items(list: Node, marker: (index: number) => string): string {
  const out: string[] = [];
  list.forEach((item, _, index) => {
    const m = marker(index);
    // A nested list right under its item's text keeps the list tight.
    const body = blocksOf(item)
      .map((b, i) => (i === 0 ? "" : b.node.type.name === "paragraph" ? "\n\n" : "\n") + b.text)
      .join("");
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

const carries = (node: Node | undefined, mark: Mark) => marksOf(node).some((m) => sameMark(m, mark));

/**
 * A paragraph's text and marks. Marks that cross are closed and reopened. A run of `*` right next
 * to one of another mark would read differently, as in `****` or `***甲*乙*丙***`, so such a mark
 * uses underscores: one reopened right after a closing, and one opened together with an inner mark
 * that closes and opens again inside it.
 */
function inline(para: Node): string {
  const children: Node[] = [];
  para.forEach((child) => children.push(child));
  // How many children in a row from `index` carry the mark, so the longest is opened outermost.
  const extent = (mark: Mark, index: number) => {
    let i = index;
    while (i < children.length && carries(children[i], mark)) i++;
    return i - index;
  };
  let out = "";
  let open: Open[] = [];
  // Spaces at the end of marked text, written after its closing: delimiters next to spaces would
  // not count as emphasis.
  let space = "";
  children.forEach((child, index) => {
    // Marks close before a break: one kept open across it, but not by the text after it, would be
    // closed after the backslash and leave it as text.
    const marks = child.type.name === "hardBreak" ? [] : marksOf(child);
    // Marks stay open up to the first that this child does not have.
    let keep = open.findIndex((o) => !marks.some((m) => sameMark(m, o)));
    if (keep === -1) keep = open.length;
    const closing = open.slice(keep).toReversed();
    out += closing.map((o) => o.close).join("") + space;
    open = open.slice(0, keep);
    space = "";
    if (child.type.name === "hardBreak") {
      // A break with only spaces after it shows nothing, and its backslash would be read as text.
      // A backslash, unlike two spaces, keeps two breaks in a row from reading as a new paragraph.
      if (children.slice(index + 1).some((c) => (c.text ?? "").trim() !== "")) out += "\\\n";
      return;
    }
    const text = child.text ?? "";
    const middle = text.trim();
    if (middle === "") {
      out += text;
      return;
    }
    const opening = marks
      .filter((m) => !open.some((o) => sameMark(o, m)))
      .toSorted((a, b) => extent(b, index) - extent(a, index));
    out += opening.length > 0 ? text.slice(0, text.indexOf(middle)) : "";
    for (const [k, mark] of opening.entries()) {
      const afterClosing = k === 0 && closing.length > 0 && out.endsWith("*");
      const inner = opening.slice(k + 1).find((m) => m.name !== "link");
      const reopens =
        inner !== undefined &&
        children.slice(index + extent(inner, index), index + extent(mark, index)).some((c) => carries(c, inner));
      const o: Open = { ...mark, close: closer(mark, afterClosing || reopens) };
      if (mark.name === "link") {
        // A "!" right before a link would make it an image.
        if (out.endsWith("!")) out = `${out.slice(0, -1)}\\!`;
        out += "[";
      } else out += o.close;
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

function closer(mark: Mark, underscores: boolean): string {
  switch (mark.name) {
    case "bold":
      return underscores ? "__" : "**";
    case "italic":
      return underscores ? "_" : "*";
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
