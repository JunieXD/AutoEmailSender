# HTML Template Preview Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a long-lived `渲染预览 / 原 HTML` toggle to the identity template HTML field so the modal defaults to a sanitized, read-only preview and only allows editing after switching to source mode.

**Architecture:** Keep the backend and saved HTML contract unchanged. Add a small frontend-only sanitization helper for preview rendering, a focused HTML field component that owns the local preview/source toggle state, and wire that component into the existing `OutreachTemplateModal` without expanding the feature to other pages.

**Tech Stack:** React 19, Vite 8, TypeScript, DOMPurify, Vitest, jsdom, Testing Library

---

## File Map

- Modify: `frontend/package.json`
  Add runtime sanitization dependency plus a minimal frontend test command.
- Modify: `frontend/package-lock.json`
  Record the installed dependency and test tooling versions.
- Modify: `frontend/vite.config.ts`
  Add a minimal Vitest configuration that works with the existing alias setup.
- Create: `frontend/test/setup.ts`
  Load `@testing-library/jest-dom/vitest` once for component assertions.
- Create: `frontend/test/htmlPreview.test.ts`
  Lock the sanitization contract before implementation.
- Create: `frontend/test/HtmlTemplateEditorField.test.tsx`
  Lock the preview/source UI behavior before wiring the page.
- Create: `frontend/src/lib/htmlPreview.ts`
  Hold preview-only sanitization and “has renderable content” helpers.
- Create: `frontend/src/components/molecules/HtmlTemplateEditorField.tsx`
  Render the persistent `渲染预览 / 原 HTML` toggle, empty state, read-only preview, and source textarea.
- Modify: `frontend/src/pages/ProfilePage.tsx`
  Replace the inline HTML textarea block inside `OutreachTemplateModal` with the new field component.

## Task 1: Add the minimal frontend test harness and lock the preview helper contract

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Modify: `frontend/vite.config.ts`
- Create: `frontend/test/setup.ts`
- Create: `frontend/test/htmlPreview.test.ts`

- [ ] **Step 1: Add the test command and dependencies**

Run:

```bash
cd frontend
npm install dompurify
npm install -D vitest jsdom @testing-library/react @testing-library/jest-dom
```

Then make `package.json` look like this:

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "eslint .",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "@tailwindcss/vite": "^4.2.2",
    "clsx": "^2.1.1",
    "dompurify": "^3.2.6",
    "lucide-react": "^1.7.0",
    "react": "^19.2.4",
    "react-dom": "^19.2.4",
    "react-markdown": "^10.1.0",
    "react-router-dom": "^7.13.2"
  },
  "devDependencies": {
    "@eslint/js": "^9.39.4",
    "@testing-library/jest-dom": "^6.7.0",
    "@testing-library/react": "^16.3.0",
    "@types/node": "^24.12.0",
    "@types/react": "^19.2.14",
    "@types/react-dom": "^19.2.3",
    "@vitejs/plugin-react": "^6.0.1",
    "autoprefixer": "^10.4.27",
    "eslint": "^9.39.4",
    "eslint-plugin-react-hooks": "^7.0.1",
    "eslint-plugin-react-refresh": "^0.5.2",
    "globals": "^17.4.0",
    "jsdom": "^26.1.0",
    "postcss": "^8.5.8",
    "tailwindcss": "^4.2.2",
    "typescript": "~5.9.3",
    "typescript-eslint": "^8.57.0",
    "vite": "^8.0.1",
    "vitest": "^3.2.4"
  }
}
```

- [ ] **Step 2: Configure Vitest with the existing Vite alias**

Update `frontend/vite.config.ts` to:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./test/setup.ts",
  },
});
```

Create `frontend/test/setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 3: Write the failing helper test first**

Create `frontend/test/htmlPreview.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  hasRenderablePreviewContent,
  sanitizeTemplateHtmlForPreview,
} from "@/lib/htmlPreview";

describe("sanitizeTemplateHtmlForPreview", () => {
  it("keeps placeholders and safe formatting", () => {
    const result = sanitizeTemplateHtmlForPreview(
      '<p style="text-align:left">{{name}}老师您好，<strong>我是{{sender_name}}</strong>。</p>',
    );

    expect(result).toContain("{{name}}老师您好");
    expect(result).toContain("{{sender_name}}");
    expect(result).toContain("<strong>");
    expect(result).toContain('style="text-align:left;"');
  });

  it("removes scripts, event handlers, and javascript urls", () => {
    const result = sanitizeTemplateHtmlForPreview(
      '<p onclick="alert(1)">正文</p><script>alert(1)</script><a href="javascript:alert(2)">链接</a>',
    );

    expect(result).toContain("正文");
    expect(result).not.toContain("onclick");
    expect(result).not.toContain("<script");
    expect(result).not.toContain("javascript:");
  });
});

describe("hasRenderablePreviewContent", () => {
  it("returns false for empty or fully stripped html", () => {
    expect(hasRenderablePreviewContent("")).toBe(false);
    expect(hasRenderablePreviewContent("<script>alert(1)</script>")).toBe(false);
  });

  it("returns true for visible html", () => {
    expect(hasRenderablePreviewContent("<p>{{name}}老师您好，</p>")).toBe(true);
  });
});
```

- [ ] **Step 4: Run the helper test and verify it fails because the module does not exist yet**

Run:

```bash
cd frontend
npx vitest run test/htmlPreview.test.ts
```

Expected:

```text
FAIL  test/htmlPreview.test.ts
Error: Failed to resolve import "@/lib/htmlPreview"
```

- [ ] **Step 5: Commit the failing-test harness**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/test/setup.ts frontend/test/htmlPreview.test.ts
git commit -m "test(frontend): add HTML preview helper test harness"
```

## Task 2: Implement the preview sanitization helper

**Files:**
- Create: `frontend/src/lib/htmlPreview.ts`
- Test: `frontend/test/htmlPreview.test.ts`

- [ ] **Step 1: Write the minimal helper implementation**

Create `frontend/src/lib/htmlPreview.ts`:

```ts
import DOMPurify from "dompurify";

const PREVIEW_ALLOWED_TAGS = [
  "a",
  "b",
  "blockquote",
  "br",
  "code",
  "div",
  "em",
  "i",
  "li",
  "ol",
  "p",
  "span",
  "strong",
  "table",
  "tbody",
  "td",
  "th",
  "thead",
  "tr",
  "u",
  "ul",
];

const PREVIEW_ALLOWED_ATTR = [
  "align",
  "cellpadding",
  "cellspacing",
  "colspan",
  "href",
  "rowspan",
  "style",
  "target",
];

const normalizePreviewHtml = (value: string) =>
  value.replace(/\s+(?=<)/g, "").trim();

export const sanitizeTemplateHtmlForPreview = (value: string): string => {
  const trimmed = value.trim();
  if (!trimmed) {
    return "";
  }

  const sanitized = DOMPurify.sanitize(trimmed, {
    ALLOWED_TAGS: PREVIEW_ALLOWED_TAGS,
    ALLOWED_ATTR: PREVIEW_ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
    FORBID_TAGS: ["script"],
  });

  return normalizePreviewHtml(sanitized);
};

export const hasRenderablePreviewContent = (value: string): boolean => {
  const sanitized = sanitizeTemplateHtmlForPreview(value);
  if (!sanitized) {
    return false;
  }

  const container = document.createElement("div");
  container.innerHTML = sanitized;
  return Boolean(container.textContent?.trim());
};
```

- [ ] **Step 2: Run the helper test and verify it passes**

Run:

```bash
cd frontend
npx vitest run test/htmlPreview.test.ts
```

Expected:

```text
✓ test/htmlPreview.test.ts
```

- [ ] **Step 3: Commit the helper**

```bash
git add frontend/src/lib/htmlPreview.ts frontend/test/htmlPreview.test.ts
git commit -m "feat(frontend): add sanitized HTML preview helper"
```

## Task 3: Add a focused HTML template field component with preview/source switching

**Files:**
- Create: `frontend/src/components/molecules/HtmlTemplateEditorField.tsx`
- Create: `frontend/test/HtmlTemplateEditorField.test.tsx`
- Test: `frontend/src/lib/htmlPreview.ts`

- [ ] **Step 1: Write the failing component interaction test**

Create `frontend/test/HtmlTemplateEditorField.test.tsx`:

```tsx
import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HtmlTemplateEditorField } from "@/components/molecules/HtmlTemplateEditorField";

const ControlledField = ({ initialValue }: { initialValue: string }) => {
  const [value, setValue] = useState(initialValue);

  return (
    <HtmlTemplateEditorField
      label="默认模板正文（HTML，可保留格式）"
      value={value}
      onChange={setValue}
      placeholder="<p>{{name}}老师您好，</p>"
    />
  );
};

describe("HtmlTemplateEditorField", () => {
  it("defaults to preview mode and hides the textarea", () => {
    render(<ControlledField initialValue="<p>{{name}}老师您好，</p>" />);

    expect(screen.getByRole("button", { name: "渲染预览" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByText("{{name}}老师您好，")).toBeInTheDocument();
  });

  it("shows the source textarea only after switching to 原 HTML", () => {
    render(<ControlledField initialValue="<p>初始正文</p>" />);

    fireEvent.click(screen.getByRole("button", { name: "原 HTML" }));

    expect(screen.getByRole("textbox")).toHaveValue("<p>初始正文</p>");
  });

  it("updates the preview after editing the source", () => {
    render(<ControlledField initialValue="<p>旧正文</p>" />);

    fireEvent.click(screen.getByRole("button", { name: "原 HTML" }));
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "<p>更新后的正文</p>" },
    });
    fireEvent.click(screen.getByRole("button", { name: "渲染预览" }));

    expect(screen.getByText("更新后的正文")).toBeInTheDocument();
  });

  it("shows the preview empty state when there is no html", () => {
    render(<ControlledField initialValue="" />);

    expect(
      screen.getByText("当前还没有 HTML 正文，切换到“原 HTML”后可直接粘贴或编辑。"),
    ).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the component test and verify it fails because the component does not exist yet**

Run:

```bash
cd frontend
npx vitest run test/HtmlTemplateEditorField.test.tsx
```

Expected:

```text
FAIL  test/HtmlTemplateEditorField.test.tsx
Error: Failed to resolve import "@/components/molecules/HtmlTemplateEditorField"
```

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/molecules/HtmlTemplateEditorField.tsx`:

```tsx
import { useState } from "react";
import clsx from "clsx";
import {
  hasRenderablePreviewContent,
  sanitizeTemplateHtmlForPreview,
} from "@/lib/htmlPreview";

type HtmlTemplateEditorFieldProps = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
};

export const HtmlTemplateEditorField = ({
  label,
  value,
  onChange,
  placeholder,
}: HtmlTemplateEditorFieldProps) => {
  const [viewMode, setViewMode] = useState<"preview" | "source">("preview");
  const previewHtml = sanitizeTemplateHtmlForPreview(value);
  const hasPreview = hasRenderablePreviewContent(value);

  return (
    <div className="block">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm font-medium text-stone-900">{label}</div>
        <div className="inline-flex rounded-full border border-stone-200 bg-stone-50 p-1">
          {[
            ["preview", "渲染预览"],
            ["source", "原 HTML"],
          ].map(([mode, title]) => {
            const active = viewMode === mode;
            return (
              <button
                key={mode}
                type="button"
                aria-pressed={active}
                onClick={() => setViewMode(mode as "preview" | "source")}
                className={clsx(
                  "rounded-full px-3 py-1.5 text-xs font-medium transition",
                  active
                    ? "bg-stone-900 text-white"
                    : "text-stone-500 hover:text-stone-800",
                )}
              >
                {title}
              </button>
            );
          })}
        </div>
      </div>

      {viewMode === "preview" ? (
        <div className="mt-3">
          {hasPreview ? (
            <div className="rounded-2xl border border-stone-200 bg-white px-4 py-4 text-sm leading-7 text-stone-700 shadow-sm">
              <div dangerouslySetInnerHTML={{ __html: previewHtml }} />
            </div>
          ) : (
            <div className="rounded-2xl border border-dashed border-stone-200 bg-white/85 px-4 py-5 text-sm leading-6 text-stone-500">
              当前还没有 HTML 正文，切换到“原 HTML”后可直接粘贴或编辑。
            </div>
          )}
          <p className="mt-2 text-xs leading-6 text-stone-500">
            预览仅用于检查排版；如需修改，请切换到“原 HTML”。
          </p>
        </div>
      ) : (
        <textarea
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className="mt-3 min-h-56 w-full rounded-2xl border border-stone-200 bg-white px-3 py-3 font-mono text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
          placeholder={placeholder}
        />
      )}
    </div>
  );
};
```

- [ ] **Step 4: Run the component test and verify it passes**

Run:

```bash
cd frontend
npx vitest run test/HtmlTemplateEditorField.test.tsx
```

Expected:

```text
✓ test/HtmlTemplateEditorField.test.tsx
```

- [ ] **Step 5: Commit the component**

```bash
git add frontend/src/components/molecules/HtmlTemplateEditorField.tsx frontend/test/HtmlTemplateEditorField.test.tsx
git commit -m "feat(frontend): add HTML template preview switcher"
```

## Task 4: Wire the new field into the identity template modal

**Files:**
- Modify: `frontend/src/pages/ProfilePage.tsx`
- Modify: `frontend/test/HtmlTemplateEditorField.test.tsx`

- [ ] **Step 1: Replace the inline HTML textarea block with the new component**

Update the imports at the top of `frontend/src/pages/ProfilePage.tsx`:

```tsx
import { HtmlTemplateEditorField } from "@/components/molecules/HtmlTemplateEditorField";
```

Then replace the HTML field block inside `OutreachTemplateModal`:

```tsx
              <HtmlTemplateEditorField
                label="默认模板正文（HTML，可保留格式）"
                value={form.outreach_template_body_html}
                onChange={onBodyHtmlChange}
                placeholder="<p>{{name}}老师您好，</p><p>我是{{sender_name}}，关注到您在{{research_direction}}方向的工作……</p>"
              />
```

Use it in place of this old block:

```tsx
              <label className="block">
                {renderFieldLabel('默认模板正文（HTML，可保留格式）')}
                <textarea
                  value={form.outreach_template_body_html}
                  onChange={(event) => onBodyHtmlChange(event.target.value)}
                  className="min-h-56 w-full rounded-2xl border border-stone-200 bg-white px-3 py-3 font-mono text-sm text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
                  placeholder="<p>{{name}}老师您好，</p><p>我是{{sender_name}}，关注到您在{{research_direction}}方向的工作……</p>"
                />
              </label>
```

- [ ] **Step 2: Add a regression assertion that source mode remains required for editing**

Extend `frontend/test/HtmlTemplateEditorField.test.tsx`:

```tsx
  it("keeps preview mode read-only until the user switches to 原 HTML", () => {
    render(<ControlledField initialValue="<p>只读预览</p>" />);

    expect(screen.getByText("只读预览")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });
```

- [ ] **Step 3: Run both frontend test files and verify they pass together**

Run:

```bash
cd frontend
npx vitest run test/htmlPreview.test.ts test/HtmlTemplateEditorField.test.tsx
```

Expected:

```text
✓ test/htmlPreview.test.ts
✓ test/HtmlTemplateEditorField.test.tsx
```

- [ ] **Step 4: Commit the page integration**

```bash
git add frontend/src/pages/ProfilePage.tsx frontend/test/HtmlTemplateEditorField.test.tsx
git commit -m "feat(frontend): wire HTML template preview into profile modal"
```

## Task 5: Run full verification and record manual checks

**Files:**
- Modify: none
- Verify: `frontend`

- [ ] **Step 1: Run the frontend test command**

Run:

```bash
cd frontend
npm run test
```

Expected:

```text
Test Files  2 passed
```

- [ ] **Step 2: Run the production build**

Run:

```bash
cd frontend
npm run build
```

Expected:

```text
✓ built in
```

- [ ] **Step 3: Manually verify the confirmed product behaviors**

Check these in the browser:

```text
1. 打开“默认发信模式与默认模板”弹窗时，HTML 区域默认停留在“渲染预览”。
2. 导入模板文件后，HTML 区域仍然停留在“渲染预览”。
3. 预览中模板变量保持原样显示，不替换为示例值。
4. 预览模式没有 textarea，必须切到“原 HTML”后才能编辑。
5. 从“原 HTML”改完内容再切回“渲染预览”，能看到更新后的排版。
6. 空 HTML 时显示空态提示。
7. 手动输入带 script 或 onclick 的 HTML 时，预览中不会执行这些内容。
```

- [ ] **Step 4: Commit the verified result**

```bash
git status --short
git commit --allow-empty -m "chore(frontend): verify HTML template preview toggle"
```
