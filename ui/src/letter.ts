import type { LetterFont, LetterStyle } from "@/api/types";
import { toHtml } from "@/markdown";

// docs/design.md#邮件格式: Latin text in the first font, Chinese in the next one the recipient has.
const FONTS: Record<LetterFont, string> = {
  song: '"Times New Roman", SimSun, "Songti SC", serif',
  hei: 'Arial, "Microsoft YaHei", "PingFang SC", sans-serif',
};

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
  const page = doc.createElement("div");
  if (style.font) page.style.fontFamily = FONTS[style.font];
  page.style.fontSize = `${style.fontSizePt}pt`;
  page.style.lineHeight = String(style.lineHeight);
  page.append(...doc.body.childNodes);
  return page.outerHTML;
}
