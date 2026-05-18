import { useEffect, useRef, useState } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Link from "@tiptap/extension-link";
import Underline from "@tiptap/extension-underline";
import TextAlign from "@tiptap/extension-text-align";
import TextStyle from "@tiptap/extension-text-style";
import TableRow from "@tiptap/extension-table-row";
import {
  Bold,
  Check,
  ChevronDown,
  Italic,
  Link2,
  Table2,
  Underline as UnderlineIcon,
} from "lucide-react";
import { deriveTextFromEmailHtml } from "@/lib/richEmail";
import { FloatingMenuPortal } from "@/components/molecules/FloatingMenuPortal";
import { FontFamily } from "@/components/molecules/tiptap/FontFamily";
import { FontSize } from "@/components/molecules/tiptap/FontSize";
import { LineHeight } from "@/components/molecules/tiptap/LineHeight";
import { FirstLineIndent } from "@/components/molecules/tiptap/FirstLineIndent";
import { BlockTypography } from "@/components/molecules/tiptap/BlockTypography";
import { TextColor } from "@/components/molecules/tiptap/TextColor";
import {
  EmailTable,
  EmailTableCell,
  EmailTableHeader,
} from "@/components/molecules/tiptap/EmailTable";
import {
  EMAIL_FIRST_LINE_INDENT_OPTIONS,
  EMAIL_FONT_OPTIONS,
  EMAIL_FONT_SIZE_OPTIONS,
  EMAIL_LINE_HEIGHT_OPTIONS,
} from "@/components/molecules/tiptap/emailEditorStyles";
import { TemplatePlaceholder } from "@/components/molecules/tiptap/TemplatePlaceholder";
import {
  TEMPLATE_PLACEHOLDER_OPTIONS,
  areTemplatePlaceholderHtmlEquivalent,
  prepareTemplateEditorHtml,
  serializeTemplatePlaceholderHtml,
  type TemplatePlaceholderKey,
} from "@/lib/templatePlaceholders";

type EmailTemplateEditorProps = {
  label: string;
  html: string;
  placeholder?: string;
  onChange: (value: { html: string; text: string }) => void;
  onFileDrop?: (file: File) => void;
};

type MenuKey = "placeholder" | "font" | "fontSize" | "lineHeight" | "indent";

type ToolbarMenuProps = {
  active: boolean;
  ariaLabel: string;
  buttonLabel: string;
  options: Array<{ label: string; value: string }>;
  selectedValue: string | null;
  onSelect: (value: string) => void;
  onToggle: () => void;
  onClose: () => void;
};

const normalizeValue = (value: string | null | undefined) =>
  String(value ?? "")
    .replace(/["']/g, "")
    .replace(/\s+/g, "")
    .toLowerCase();

const getLineHeightLabel = (value: string | null | undefined) =>
  EMAIL_LINE_HEIGHT_OPTIONS.find(
    (option) => normalizeValue(option.value) === normalizeValue(value),
  )?.label ?? "行距";

const getFirstDroppedFile = (event: Event) => {
  if (!("dataTransfer" in event)) {
    return null;
  }
  return event.dataTransfer?.files?.[0] ?? null;
};

const isFileDragEvent = (event: Event) => {
  if (!("dataTransfer" in event)) {
    return false;
  }

  const types = event.dataTransfer?.types
    ? Array.from(event.dataTransfer.types)
    : [];
  return types.includes("Files") || Boolean(event.dataTransfer?.files?.length);
};

const ToolbarMenu = ({
  active,
  ariaLabel,
  buttonLabel,
  options,
  selectedValue,
  onSelect,
  onToggle,
  onClose,
}: ToolbarMenuProps) => {
  const buttonRef = useRef<HTMLButtonElement | null>(null);

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-label={ariaLabel}
        aria-expanded={active}
        onClick={onToggle}
        className="inline-flex min-w-[88px] items-center justify-between gap-2 rounded-xl border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 transition hover:border-stone-300 hover:bg-stone-50"
      >
        <span className="truncate">{buttonLabel}</span>
        <ChevronDown className="h-4 w-4 shrink-0 text-stone-400" />
      </button>

      <FloatingMenuPortal
        open={active}
        anchorRef={buttonRef}
        minWidth={180}
        testId={ariaLabel === "占位符菜单" ? "email-template-placeholder-menu" : undefined}
        onClose={onClose}
      >
        {options.map((option) => {
          const selected = normalizeValue(selectedValue) === normalizeValue(option.value);
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => {
                onSelect(option.value);
                onClose();
              }}
              className="flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left text-sm text-stone-700 transition hover:bg-stone-50"
            >
              <span>{option.label}</span>
              {selected ? <Check className="h-4 w-4 text-primary" /> : null}
            </button>
          );
        })}
      </FloatingMenuPortal>
    </div>
  );
};

export const EmailTemplateEditor = ({
  label,
  html,
  placeholder,
  onChange,
  onFileDrop,
}: EmailTemplateEditorProps) => {
  const [openMenu, setOpenMenu] = useState<MenuKey | null>(null);
  const lastLocalHtmlRef = useRef<string | null>(null);
  const onFileDropRef = useRef<typeof onFileDrop>(onFileDrop);

  useEffect(() => {
    onFileDropRef.current = onFileDrop;
  }, [onFileDrop]);

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: {
          levels: [1, 2, 3, 4, 5, 6],
        },
      }),
      Link.configure({
        openOnClick: false,
      }),
      Underline,
      TextStyle,
      TextAlign.configure({ types: ["paragraph", "heading"] }),
      EmailTable.configure({ resizable: true }),
      TableRow,
      EmailTableHeader,
      EmailTableCell,
      TemplatePlaceholder,
      FontFamily,
      FontSize,
      TextColor,
      LineHeight,
      FirstLineIndent,
      BlockTypography,
    ],
    content: prepareTemplateEditorHtml(html),
    immediatelyRender: false,
    editorProps: {
      attributes: {
        class:
          "email-editor-content min-h-[320px] max-h-[520px] overflow-y-auto overscroll-contain rounded-[28px] border border-stone-200 bg-white px-4 py-4 text-sm leading-7 text-stone-700 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15",
        role: "textbox",
        "aria-label": label,
      },
      handleDOMEvents: {
        dragover: (_view, event) => {
          if (!onFileDropRef.current || !isFileDragEvent(event)) {
            return false;
          }

          event.preventDefault();
          return true;
        },
        drop: (_view, event) => {
          if (!onFileDropRef.current || !isFileDragEvent(event)) {
            return false;
          }

          event.preventDefault();
          event.stopPropagation();
          const file = getFirstDroppedFile(event);
          if (!file) {
            return true;
          }

          onFileDropRef.current(file);
          return true;
        },
      },
    },
    onUpdate: ({ editor: currentEditor }) => {
      const nextHtml = serializeTemplatePlaceholderHtml(currentEditor.getHTML());
      lastLocalHtmlRef.current = nextHtml;
      onChange({
        html: nextHtml,
        text: deriveTextFromEmailHtml(nextHtml),
      });
    },
  });

  useEffect(() => {
    const preparedHtml = prepareTemplateEditorHtml(html);
    if (lastLocalHtmlRef.current === html) {
      return;
    }
    if (editor && !areTemplatePlaceholderHtmlEquivalent(preparedHtml, editor.getHTML())) {
      editor.commands.setContent(preparedHtml, false);
    }
  }, [editor, html]);

  if (!editor) {
    return null;
  }

  const textStyleAttributes = editor.getAttributes("textStyle");
  const paragraphAttributes = editor.getAttributes("paragraph");

  const currentFontLabel =
    EMAIL_FONT_OPTIONS.find(
      (option) => normalizeValue(option.value) === normalizeValue(textStyleAttributes.fontFamily),
    )?.label ?? "字体";
  const currentFontSize = textStyleAttributes.fontSize || "字号";
  const currentLineHeight = getLineHeightLabel(paragraphAttributes.lineHeight);
  const currentIndent =
    paragraphAttributes.firstLineIndent === "2em" ? "首行缩进 2 字符" : "首行缩进";
  const isEditorEmpty = editor.isEmpty;
  const tableActive =
    editor.isActive("table") || editor.isActive("tableCell") || editor.isActive("tableHeader");

  return (
    <div className="block">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm font-medium text-stone-800">{label}</div>
        <div className="flex flex-wrap gap-2">
          <ToolbarMenu
            active={openMenu === "placeholder"}
            ariaLabel="占位符菜单"
            buttonLabel="占位符"
            options={TEMPLATE_PLACEHOLDER_OPTIONS.map((option) => ({
              label: option.label,
              value: option.key,
            }))}
            selectedValue={null}
            onSelect={(value) => {
              editor
                .chain()
                .focus()
                .insertTemplatePlaceholder(value as TemplatePlaceholderKey)
                .run();
            }}
            onToggle={() =>
              setOpenMenu((current) => (current === "placeholder" ? null : "placeholder"))
            }
            onClose={() => setOpenMenu(null)}
          />
          <ToolbarMenu
            active={openMenu === "font"}
            ariaLabel="字体菜单"
            buttonLabel={currentFontLabel}
            options={EMAIL_FONT_OPTIONS}
            selectedValue={textStyleAttributes.fontFamily}
            onSelect={(value) => {
              editor.chain().focus().setMark("textStyle", { fontFamily: value }).run();
            }}
            onToggle={() => setOpenMenu((current) => (current === "font" ? null : "font"))}
            onClose={() => setOpenMenu(null)}
          />
          <ToolbarMenu
            active={openMenu === "fontSize"}
            ariaLabel="字号菜单"
            buttonLabel={currentFontSize}
            options={EMAIL_FONT_SIZE_OPTIONS.map((option) => ({ label: option, value: option }))}
            selectedValue={textStyleAttributes.fontSize}
            onSelect={(value) => {
              editor.chain().focus().setMark("textStyle", { fontSize: value }).run();
            }}
            onToggle={() =>
              setOpenMenu((current) => (current === "fontSize" ? null : "fontSize"))
            }
            onClose={() => setOpenMenu(null)}
          />
          <ToolbarMenu
            active={openMenu === "lineHeight"}
            ariaLabel="行距菜单"
            buttonLabel={currentLineHeight}
            options={EMAIL_LINE_HEIGHT_OPTIONS}
            selectedValue={paragraphAttributes.lineHeight}
            onSelect={(value) => {
              editor.chain().focus().updateAttributes("paragraph", { lineHeight: value }).run();
            }}
            onToggle={() =>
              setOpenMenu((current) => (current === "lineHeight" ? null : "lineHeight"))
            }
            onClose={() => setOpenMenu(null)}
          />
          <ToolbarMenu
            active={openMenu === "indent"}
            ariaLabel="首行缩进菜单"
            buttonLabel={currentIndent}
            options={[
              { label: "无缩进", value: "0" },
              ...EMAIL_FIRST_LINE_INDENT_OPTIONS.filter((option) => option !== "0").map((option) => ({
                label: option === "2em" ? "首行缩进 2 字符" : option,
                value: option,
              })),
            ]}
            selectedValue={paragraphAttributes.firstLineIndent ?? "0"}
            onSelect={(value) => {
              editor
                .chain()
                .focus()
                .updateAttributes("paragraph", {
                  firstLineIndent: value === "0" ? null : value,
                })
                .run();
            }}
            onToggle={() => setOpenMenu((current) => (current === "indent" ? null : "indent"))}
            onClose={() => setOpenMenu(null)}
          />
        </div>
      </div>

      <div className="mb-3 flex flex-wrap gap-1 rounded-2xl border border-stone-200 bg-stone-50 p-1">
        <button
          type="button"
          aria-label="加粗"
          onClick={() => editor.chain().focus().toggleBold().run()}
          className="rounded-xl p-2 text-stone-600 hover:bg-white"
        >
          <Bold className="h-4 w-4" />
        </button>
        <button
          type="button"
          aria-label="斜体"
          onClick={() => editor.chain().focus().toggleItalic().run()}
          className="rounded-xl p-2 text-stone-600 hover:bg-white"
        >
          <Italic className="h-4 w-4" />
        </button>
        <button
          type="button"
          aria-label="下划线"
          onClick={() => editor.chain().focus().toggleUnderline().run()}
          className="rounded-xl p-2 text-stone-600 hover:bg-white"
        >
          <UnderlineIcon className="h-4 w-4" />
        </button>
        <button
          type="button"
          aria-label="插入链接"
          onClick={() =>
            editor
              .chain()
              .focus()
              .setLink({ href: "https://example.com" })
              .run()
          }
          className="rounded-xl p-2 text-stone-600 hover:bg-white"
        >
          <Link2 className="h-4 w-4" />
        </button>
        <button
          type="button"
          aria-label="插入表格"
          onClick={() =>
            editor
              .chain()
              .focus()
              .insertTable({ rows: 2, cols: 2, withHeaderRow: false })
              .run()
          }
          className="rounded-xl p-2 text-stone-600 hover:bg-white"
        >
          <Table2 className="h-4 w-4" />
        </button>
      </div>

      {tableActive ? (
        <div
          role="group"
          aria-label="表格操作"
          className="mb-3 flex flex-wrap gap-1 rounded-2xl border border-primary/15 bg-primary/5 p-1.5"
        >
          <button
            type="button"
            aria-label="上方插入行"
            onClick={() => editor.chain().focus().addRowBefore().run()}
            className="rounded-xl px-3 py-2 text-xs font-medium text-stone-700 hover:bg-white"
          >
            上方插入行
          </button>
          <button
            type="button"
            aria-label="下方插入行"
            onClick={() => editor.chain().focus().addRowAfter().run()}
            className="rounded-xl px-3 py-2 text-xs font-medium text-stone-700 hover:bg-white"
          >
            下方插入行
          </button>
          <button
            type="button"
            aria-label="左侧插入列"
            onClick={() => editor.chain().focus().addColumnBefore().run()}
            className="rounded-xl px-3 py-2 text-xs font-medium text-stone-700 hover:bg-white"
          >
            左侧插入列
          </button>
          <button
            type="button"
            aria-label="右侧插入列"
            onClick={() => editor.chain().focus().addColumnAfter().run()}
            className="rounded-xl px-3 py-2 text-xs font-medium text-stone-700 hover:bg-white"
          >
            右侧插入列
          </button>
          <button
            type="button"
            aria-label="删除行"
            onClick={() => editor.chain().focus().deleteRow().run()}
            className="rounded-xl px-3 py-2 text-xs font-medium text-stone-700 hover:bg-white"
          >
            删除行
          </button>
          <button
            type="button"
            aria-label="删除列"
            onClick={() => editor.chain().focus().deleteColumn().run()}
            className="rounded-xl px-3 py-2 text-xs font-medium text-stone-700 hover:bg-white"
          >
            删除列
          </button>
          <button
            type="button"
            aria-label="合并单元格"
            onClick={() => editor.chain().focus().mergeCells().run()}
            className="rounded-xl px-3 py-2 text-xs font-medium text-stone-700 hover:bg-white"
          >
            合并单元格
          </button>
          <button
            type="button"
            aria-label="拆分单元格"
            onClick={() => editor.chain().focus().splitCell().run()}
            className="rounded-xl px-3 py-2 text-xs font-medium text-stone-700 hover:bg-white"
          >
            拆分单元格
          </button>
          <button
            type="button"
            aria-label="删除表格"
            onClick={() => editor.chain().focus().deleteTable().run()}
            className="rounded-xl px-3 py-2 text-xs font-medium text-primary hover:bg-white"
          >
            删除表格
          </button>
        </div>
      ) : null}

      <div className="relative">
        {placeholder && isEditorEmpty ? (
          <div className="pointer-events-none absolute left-4 top-4 z-10 text-sm leading-7 text-stone-400">
            {placeholder}
          </div>
        ) : null}
        <EditorContent editor={editor} />
      </div>
    </div>
  );
};
