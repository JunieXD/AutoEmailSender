import {
  normalizeFontSizeStyle,
  normalizeFontSizeValue,
} from "@/lib/fontSize";

export type TemplatePlaceholderKey =
  | "name"
  | "email"
  | "title"
  | "university"
  | "school"
  | "department"
  | "research_direction"
  | "sender_name"
  | "sender_email"
  | "year"
  | "month"
  | "day";

export type TemplatePlaceholderOption = {
  key: TemplatePlaceholderKey;
  label: string;
  token: string;
};

export type TemplatePlaceholderSegment =
  | { type: "text"; value: string }
  | {
      type: "placeholder";
      key: TemplatePlaceholderKey;
      label: string;
      token: string;
    };

export const TEMPLATE_PLACEHOLDER_OPTIONS: TemplatePlaceholderOption[] = [
  { key: "name", label: "导师姓名", token: "{{name}}" },
  { key: "email", label: "导师邮箱", token: "{{email}}" },
  { key: "title", label: "导师职称", token: "{{title}}" },
  { key: "university", label: "导师学校", token: "{{university}}" },
  { key: "school", label: "导师学院", token: "{{school}}" },
  { key: "department", label: "导师院系", token: "{{department}}" },
  { key: "research_direction", label: "研究方向", token: "{{research_direction}}" },
  { key: "sender_name", label: "发件人姓名", token: "{{sender_name}}" },
  { key: "sender_email", label: "发件邮箱", token: "{{sender_email}}" },
  { key: "year", label: "发送年份", token: "{{year}}" },
  { key: "month", label: "发送月份", token: "{{month}}" },
  { key: "day", label: "发送日期", token: "{{day}}" },
];

export const getTemplatePlaceholder = (key: string | null | undefined) =>
  TEMPLATE_PLACEHOLDER_OPTIONS.find((option) => option.key === key);

const createTemplateTokenPattern = () =>
  /\{\{\s*(name|email|title|university|school|department|research_direction|sender_name|sender_email|year|month|day)\s*\}\}/g;

export const parseTemplatePlaceholderText = (text: string) => {
  const segments: TemplatePlaceholderSegment[] = [];
  const tokenPattern = createTemplateTokenPattern();
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = tokenPattern.exec(text)) !== null) {
    const [token, key] = match as RegExpExecArray & [string, TemplatePlaceholderKey];

    if (match.index > cursor) {
      segments.push({ type: "text", value: text.slice(cursor, match.index) });
    }

    const option = getTemplatePlaceholder(key);
    if (option) {
      segments.push({
        type: "placeholder",
        key,
        label: option.label,
        token: option.token,
      });
    } else {
      segments.push({ type: "text", value: token });
    }

    cursor = match.index + token.length;
  }

  if (cursor < text.length) {
    segments.push({ type: "text", value: text.slice(cursor) });
  }

  return segments;
};

export const prepareTemplatePlaceholderHtml = (html: string) =>
  html.replace(createTemplateTokenPattern(), (_match, key: TemplatePlaceholderKey) => {
    const option = getTemplatePlaceholder(key);
    return `<span data-template-placeholder="${key}">${option?.label ?? key}</span>`;
  });

const convertFontTagsToSpanStyles = (html: string) => {
  const container = document.createElement("div");
  container.innerHTML = html;

  container.querySelectorAll("font").forEach((fontElement) => {
    const span = document.createElement("span");
    const styleParts: string[] = [];
    const face = fontElement.getAttribute("face")?.trim();
    const size = normalizeFontSizeValue(fontElement.getAttribute("size"));
    const color = fontElement.getAttribute("color")?.trim();
    const existingStyle = fontElement.getAttribute("style")?.trim();

    if (face) {
      styleParts.push(`font-family:${face}`);
    }
    if (size) {
      styleParts.push(`font-size:${size}`);
    }
    if (color) {
      styleParts.push(`color:${color}`);
    }
    if (existingStyle) {
      const normalizedStyle = normalizeFontSizeStyle(existingStyle);
      if (normalizedStyle) {
        styleParts.push(normalizedStyle.replace(/;+\s*$/, ""));
      }
    }
    if (styleParts.length > 0) {
      span.setAttribute("style", `${styleParts.join(";")};`);
    }

    while (fontElement.firstChild) {
      span.appendChild(fontElement.firstChild);
    }
    fontElement.replaceWith(span);
  });

  container.querySelectorAll<HTMLElement>("[style]").forEach((element) => {
    const normalizedStyle = normalizeFontSizeStyle(element.getAttribute("style"));
    if (normalizedStyle) {
      element.setAttribute("style", normalizedStyle);
    }
  });

  return container.innerHTML;
};

export const prepareTemplateEditorHtml = (html: string) =>
  prepareTemplatePlaceholderHtml(convertFontTagsToSpanStyles(html));

export const serializeTemplatePlaceholderHtml = (html: string) =>
  html.replace(
    /<span[^>]*data-template-placeholder=["']([^"']+)["'][^>]*>.*?<\/span>/g,
    (_match, key: string) => getTemplatePlaceholder(key)?.token ?? "",
  );

const normalizeTemplatePlaceholderTokens = (html: string) =>
  html.replace(createTemplateTokenPattern(), (_match, key: TemplatePlaceholderKey) => {
    const option = getTemplatePlaceholder(key);
    return option?.token ?? `{{${key}}}`;
  });

const normalizeColorValue = (value: string) => {
  const trimmed = value.trim();
  const hexMatch = trimmed.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!hexMatch) {
    return trimmed.replace(/\s*,\s*/g, ",").replace(/\s+/g, " ");
  }

  const hex = hexMatch[1].length === 3
    ? hexMatch[1].split("").map((item) => `${item}${item}`).join("")
    : hexMatch[1];
  const red = Number.parseInt(hex.slice(0, 2), 16);
  const green = Number.parseInt(hex.slice(2, 4), 16);
  const blue = Number.parseInt(hex.slice(4, 6), 16);
  return `rgb(${red},${green},${blue})`;
};

const normalizeCssValue = (property: string, value: string) => {
  const normalizedWhitespace = value.trim().replace(/\s+/g, " ");

  if (property === "font-size") {
    return normalizeFontSizeValue(normalizedWhitespace) ?? normalizedWhitespace;
  }

  if (property === "font-family") {
    return normalizedWhitespace
      .split(",")
      .map((family) => family.trim().replace(/^["']|["']$/g, ""))
      .filter(Boolean)
      .join(",");
  }

  if (property === "color" || property.endsWith("-color")) {
    return normalizeColorValue(normalizedWhitespace);
  }

  return normalizedWhitespace
    .replace(/\s*,\s*/g, ",")
    .replace(/\s*:\s*/g, ":")
    .replace(/\s*;\s*/g, ";");
};

const normalizeInlineStyleForCompare = (styleValue: string) => {
  const declarations = styleValue
    .split(";")
    .map((declaration) => declaration.trim())
    .filter(Boolean)
    .map((declaration) => {
      const separatorIndex = declaration.indexOf(":");
      if (separatorIndex < 0) {
        return null;
      }

      const property = declaration.slice(0, separatorIndex).trim().toLowerCase();
      const value = declaration.slice(separatorIndex + 1).trim();
      if (!property || !value) {
        return null;
      }

      return {
        property,
        value: normalizeCssValue(property, value),
      };
    })
    .filter((declaration): declaration is { property: string; value: string } =>
      Boolean(declaration),
    )
    .sort((left, right) =>
      left.property === right.property
        ? left.value.localeCompare(right.value)
        : left.property.localeCompare(right.property),
    );

  return declarations.map(({ property, value }) => `${property}:${value}`).join(";");
};

const parseHtmlAttributes = (attributeSource: string) => {
  const attributes: Array<{ name: string; value: string | null }> = [];
  const attributePattern = /([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
  let match: RegExpExecArray | null;

  while ((match = attributePattern.exec(attributeSource)) !== null) {
    const [, rawName, doubleQuotedValue, singleQuotedValue, unquotedValue] = match;
    const name = rawName.toLowerCase();
    const rawValue = doubleQuotedValue ?? singleQuotedValue ?? unquotedValue ?? null;
    const value = name === "style" && rawValue != null
      ? normalizeInlineStyleForCompare(rawValue)
      : rawValue?.trim().replace(/\s+/g, " ") ?? null;

    if (value === "") {
      continue;
    }

    attributes.push({ name, value });
  }

  return attributes.sort((left, right) =>
    left.name === right.name
      ? String(left.value ?? "").localeCompare(String(right.value ?? ""))
      : left.name.localeCompare(right.name),
  );
};

const canonicalizeHtmlTagForCompare = (tag: string) => {
  if (tag.startsWith("<!--")) {
    return "";
  }

  const endTagMatch = tag.match(/^<\s*\/\s*([^\s>]+)\s*>$/);
  if (endTagMatch) {
    return `</${endTagMatch[1].toLowerCase()}>`;
  }

  const startTagMatch = tag.match(/^<\s*([^\s/>]+)([\s\S]*?)(\/?)\s*>$/);
  if (!startTagMatch) {
    return tag.trim();
  }

  const [, rawTagName, attributeSource, selfClosing] = startTagMatch;
  const tagName = rawTagName.toLowerCase();
  const attributes = parseHtmlAttributes(attributeSource);
  const serializedAttributes = attributes
    .map(({ name, value }) => (value == null ? name : `${name}="${value}"`))
    .join(" ");

  const suffix = selfClosing ? " /" : "";
  return serializedAttributes
    ? `<${tagName} ${serializedAttributes}${suffix}>`
    : `<${tagName}${suffix}>`;
};

export const normalizeTemplatePlaceholderHtmlForCompare = (html: string) =>
  normalizeTemplatePlaceholderTokens(serializeTemplatePlaceholderHtml(html))
    .trim()
    .replace(/<!--[\s\S]*?-->|<\/?[a-zA-Z][^>]*>/g, canonicalizeHtmlTagForCompare)
    .replace(/>\s+</g, "><")
    .replace(/\s+/g, " ");

export const areTemplatePlaceholderHtmlEquivalent = (leftHtml: string, rightHtml: string) =>
  normalizeTemplatePlaceholderHtmlForCompare(leftHtml) ===
  normalizeTemplatePlaceholderHtmlForCompare(rightHtml);
