import { Plugin } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import { type Editor, EditorContent, Extension, useEditor, useEditorState } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Bold, Braces, Italic, Link, List, ListOrdered } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { FIELD, Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { linkable, toHtml, toMarkdown } from "@/markdown";

/** The formats docs/design.md#邮件格式 allows, which `toMarkdown` writes. */
export const letterFormats = StarterKit.configure({
  blockquote: false,
  code: false,
  codeBlock: false,
  heading: false,
  horizontalRule: false,
  strike: false,
  underline: false,
  // Not autolinking also keeps what is typed after a link out of it.
  link: { openOnClick: false, autolink: false, isAllowedUri: linkable },
});

/** How the editor reads a letter's HTML: spaces typed in it, such as an indent, are text. */
export const letterParsing = { preserveWhitespace: true } as const;

/**
 * Highlights the placeholders the app fills in, such as `{导师姓名}`, so a mistyped one stands out by
 * staying plain.
 */
function highlightPlaceholders(names: string[]) {
  const pattern = new RegExp(`\\{(?:${names.join("|")})\\}`, "g");
  return Extension.create({
    name: "highlightPlaceholders",
    addProseMirrorPlugins() {
      return [
        new Plugin({
          props: {
            decorations(state) {
              const found: Decoration[] = [];
              state.doc.descendants((node, pos) => {
                for (const m of node.text?.matchAll(pattern) ?? []) {
                  found.push(
                    Decoration.inline(pos + m.index, pos + m.index + m[0].length, {
                      class: "rounded-sm bg-selected text-brand-text",
                    }),
                  );
                }
              });
              return DecorationSet.create(state.doc, found);
            },
          },
        }),
      ];
    },
  });
}

/**
 * The body of a letter as Markdown, in the formats docs/design.md#邮件格式 allows: paragraphs, line
 * breaks, bold, italic, links and lists. Pasted text keeps only these. Uncontrolled: `markdown` is
 * only read when mounting.
 */
export function LetterEditor({
  id,
  markdown,
  placeholders,
  disabled,
  onChange,
}: {
  id: string;
  markdown: string;
  placeholders: string[];
  /** While the letter is being saved, so nothing typed meanwhile goes unsaved. */
  disabled: boolean;
  onChange: (markdown: string) => void;
}) {
  const editor = useEditor({
    extensions: [letterFormats, highlightPlaceholders(placeholders)],
    content: toHtml(markdown),
    parseOptions: letterParsing,
    editorProps: {
      attributes: {
        id,
        class: `${FIELD} min-h-48 py-2.5 leading-relaxed [&_a]:text-brand-text [&_a]:underline [&_ol]:list-decimal [&_ol]:pl-6 [&>*+*]:mt-3 [&_ul]:list-disc [&_ul]:pl-6`,
      },
    },
    onUpdate: ({ editor: e }) => onChange(toMarkdown(e.state.doc)),
  });
  useEffect(() => editor.setEditable(!disabled, false), [editor, disabled]);
  return (
    <div className="flex flex-col gap-1.5">
      <Toolbar editor={editor} placeholders={placeholders} />
      <EditorContent editor={editor} />
    </div>
  );
}

function Toolbar({ editor, placeholders }: { editor: Editor; placeholders: string[] }) {
  const active = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e.isActive("bold"),
      italic: e.isActive("italic"),
      link: e.isActive("link"),
      bulletList: e.isActive("bulletList"),
      orderedList: e.isActive("orderedList"),
    }),
  });
  const mark = "aria-pressed:bg-selected aria-pressed:text-brand-text";
  return (
    <div className="flex flex-wrap items-center gap-0.5" role="toolbar" aria-label="格式">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className={mark}
        aria-label="加粗"
        aria-pressed={active.bold}
        onClick={() => editor.chain().focus().toggleBold().run()}
      >
        <Bold strokeWidth={1.8} />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className={mark}
        aria-label="斜体"
        aria-pressed={active.italic}
        onClick={() => editor.chain().focus().toggleItalic().run()}
      >
        <Italic strokeWidth={1.8} />
      </Button>
      <LinkButton editor={editor} active={active.link} className={mark} />
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className={mark}
        aria-label="无序列表"
        aria-pressed={active.bulletList}
        onClick={() => editor.chain().focus().toggleBulletList().run()}
      >
        <List strokeWidth={1.8} />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className={mark}
        aria-label="有序列表"
        aria-pressed={active.orderedList}
        onClick={() => editor.chain().focus().toggleOrderedList().run()}
      >
        <ListOrdered strokeWidth={1.8} />
      </Button>
      <PlaceholderMenu editor={editor} placeholders={placeholders} />
    </div>
  );
}

/** Where a typed address points: as typed when it has a scheme, else an email or a web address. */
function linkAddress(typed: string): string {
  if (/^[a-z][a-z\d+.-]*:/i.test(typed)) return typed;
  return /^[^\s@/]+@[^\s@/]+$/.test(typed) ? `mailto:${typed}` : `https://${typed}`;
}

/** Asks for the address; with nothing selected, the address as typed becomes the linked text. */
function LinkButton({ editor, active, className }: { editor: Editor; active: boolean; className: string }) {
  const [open, setOpen] = useState(false);
  const [href, setHref] = useState("");
  const [refused, setRefused] = useState(false);
  function apply(typed: string) {
    const chain = editor.chain().focus().extendMarkRange("link");
    const address = linkAddress(typed);
    if (!typed) chain.unsetLink().run();
    else if (editor.state.selection.empty && !active)
      chain.insertContent({ type: "text", text: typed, marks: [{ type: "link", attrs: { href: address } }] }).run();
    else chain.setLink({ href: address }).run();
  }
  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setHref(editor.getAttributes("link").href ?? "");
          setRefused(false);
        }
        setOpen(next);
      }}
    >
      <PopoverTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className={className}
            aria-label="链接"
            aria-pressed={active}
          />
        }
      >
        <Link strokeWidth={1.8} />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-2" finalFocus={false}>
        <form
          className="flex flex-col gap-1.5"
          onSubmit={(e) => {
            // The popover is portalled out of the letter's own form, but React events still reach it.
            e.preventDefault();
            e.stopPropagation();
            const typed = href.trim();
            if (typed && !linkable(linkAddress(typed))) {
              setRefused(true);
              return;
            }
            setOpen(false);
            apply(typed);
          }}
        >
          <div className="flex gap-1.5">
            <Input
              aria-label="链接地址"
              aria-invalid={refused}
              placeholder="https://"
              spellCheck={false}
              value={href}
              onChange={(e) => {
                setHref(e.target.value);
                setRefused(false);
              }}
            />
            <Button type="submit">{href.trim() || !active ? "确定" : "去掉链接"}</Button>
          </div>
          {refused && <p className="text-sm text-destructive">只能链接到网页或邮箱地址。</p>}
        </form>
      </PopoverContent>
    </Popover>
  );
}

function PlaceholderMenu({ editor, placeholders }: { editor: Editor; placeholders: string[] }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={<Button type="button" variant="ghost" className="px-2.5" />}>
        <Braces strokeWidth={1.8} />
        插入占位符
      </PopoverTrigger>
      <PopoverContent align="start" className="flex w-44 flex-col" finalFocus={false}>
        {placeholders.map((name) => (
          <button
            key={name}
            type="button"
            className="h-9 rounded-md px-3 text-left hover:bg-muted"
            onClick={() => {
              setOpen(false);
              editor
                .chain()
                .focus()
                .insertContent({ type: "text", text: `{${name}}` })
                .run();
            }}
          >
            {name}
          </button>
        ))}
      </PopoverContent>
    </Popover>
  );
}
