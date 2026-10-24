import type { LetterFont, LetterStyle } from "@/api/types";
import { toHtml } from "@/markdown";

// docs/design.md#邮件格式: Latin text in the first font, Chinese in the next one the recipient has.
const FONTS: Record<LetterFont, string> = {
  song: '"Times New Roman", SimSun, "Songti SC", serif',
  hei: 'Arial, "Microsoft YaHei", "PingFang SC", sans-serif',
};

/** How the app shows a letter's body, in the editor and in the correspondence. */
export const LETTER_BODY =
  "leading-relaxed [&_a]:text-brand-text [&_a]:underline [&_ol]:list-decimal [&_ol]:pl-6 [&>*+*]:mt-3 [&_ul]:list-disc [&_ul]:pl-6";

/** A letter's body as the HTML sent, in the letter style. Styles are inline: mail apps drop style sheets. */
export function letterHtml(markdown: string, style: LetterStyle): string {
  const doc = new DOMParser().parseFromString(toHtml(markdown), "text/html");
  const spacing = `0 0 ${style.paragraphSpacingEm}em`;
  for (const p of doc.querySelectorAll("p")) {
    p.style.margin = spacing;
    if (style.firstLineIndent && !p.closest("li")) p.style.textIndent = "2em";
  }
  for (const list of doc.querySelectorAll<HTMLElement>("ul, ol")) {
    list.style.margin = list.parentElement?.closest("li") ? "0" : spacing;
    list.style.paddingLeft = "2em";
    // Written out, as the app's own styles turn list markers off where the letter is previewed.
    list.style.listStyleType = list.tagName === "OL" ? "decimal" : "disc";
  }
  // Spaces typed in a letter show as typed: HTML would drop one starting a line or following another.
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  let before = ""; // The character before, none at the start of a line.
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (node instanceof Text) {
      const text = node.data;
      node.data = text.replace(/ /g, (space, i: number) => {
        const previous = i === 0 ? before : text[i - 1];
        return previous === "" || previous === " " ? "\u00a0" : space;
      });
      before = text.at(-1) ?? before;
    } else if (["P", "LI", "BR"].includes(node.nodeName)) before = "";
  }
  const page = doc.createElement("div");
  if (style.font) page.style.fontFamily = FONTS[style.font];
  page.style.fontSize = `${style.fontSizePt}pt`;
  page.style.lineHeight = String(style.lineHeight);
  page.append(...doc.body.childNodes);
  return page.outerHTML;
}

/** A letter's body as plain text, for mail apps that show no HTML. */
export function letterText(markdown: string): string {
  const doc = new DOMParser().parseFromString(toHtml(markdown), "text/html");
  return blocks(doc.body, "").join("\n\n");
}

/** Paragraphs and lists, each a block of lines. */
function blocks(parent: Element, indent: string): string[] {
  return [...parent.children].map((el) => (isList(el) ? listText(el, indent) : indent + inline(el)));
}

function listText(el: Element, indent: string): string {
  const start = Number(el.getAttribute("start") ?? 1);
  return [...el.children]
    .map((item, i) => {
      const marker = el.tagName === "OL" ? `${start + i}. ` : "- ";
      const pad = indent + " ".repeat(marker.length);
      // In the item's order: its text on the marker's line, then nested lists and further paragraphs.
      const out: string[] = [];
      let text: string | undefined;
      const flush = () => {
        if (text === undefined) return;
        out.push((out.length === 0 ? indent + marker : pad) + text.replaceAll("\n", `\n${pad}`));
        text = undefined;
      };
      for (const node of item.childNodes) {
        if (node instanceof Text && /^\n*$/.test(node.data)) continue;
        if (node instanceof Element && isList(node)) {
          if (out.length === 0) text ??= "";
          flush();
          out.push(listText(node, pad));
        } else if (node instanceof Element && node.tagName === "P") {
          flush();
          text = inline(node);
        } else text = (text ?? "") + inline(node);
      }
      flush();
      return out.join("\n");
    })
    .join("\n");
}

function isList(el: Element): boolean {
  return el.tagName === "UL" || el.tagName === "OL";
}

/** Text with its line breaks; a link keeps its address unless the text already shows it. */
function inline(node: Node): string {
  if (node instanceof HTMLBRElement) return "\n";
  // Line breaks in the HTML's own text only lay out the markup; the letter's are <br>.
  if (!(node instanceof Element)) return (node.textContent ?? "").replaceAll("\n", "");
  const text = [...node.childNodes].map(inline).join("");
  if (!(node instanceof HTMLAnchorElement)) return text;
  const address = node.getAttribute("href")?.replace(/^mailto:/, "") ?? "";
  return text === address ? text : `${text} <${address}>`;
}
