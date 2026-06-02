import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type KeyboardEvent,
} from "react";
import { Braces, ChevronDown } from "lucide-react";
import { FloatingMenuPortal } from "@/components/molecules/FloatingMenuPortal";
import {
  TEMPLATE_PLACEHOLDER_OPTIONS,
  getTemplatePlaceholder,
  parseTemplatePlaceholderText,
  type TemplatePlaceholderOption,
} from "@/lib/templatePlaceholders";

type SubjectTemplateInputProps = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  required?: boolean;
  disabled?: boolean;
  className?: string;
  inputClassName?: string;
};

const createPlaceholderChip = (option: TemplatePlaceholderOption) => {
  const chip = document.createElement("span");
  chip.className = "email-placeholder-chip";
  chip.dataset.templatePlaceholder = option.key;
  chip.contentEditable = "false";
  chip.textContent = option.label;
  chip.setAttribute("aria-label", option.label);
  return chip;
};

const renderSubjectValue = (editor: HTMLElement, value: string) => {
  const fragment = document.createDocumentFragment();

  parseTemplatePlaceholderText(value).forEach((segment) => {
    if (segment.type === "text") {
      fragment.append(document.createTextNode(segment.value));
      return;
    }

    fragment.append(createPlaceholderChip(segment));
  });

  editor.replaceChildren(fragment);
};

const serializeSubjectNode = (node: Node): string => {
  if (node.nodeType === Node.TEXT_NODE) {
    return node.textContent ?? "";
  }

  if (node.nodeType !== Node.ELEMENT_NODE) {
    return "";
  }

  const element = node as HTMLElement;
  const placeholderKey = element.dataset.templatePlaceholder;
  if (placeholderKey) {
    return getTemplatePlaceholder(placeholderKey)?.token ?? "";
  }

  if (element.tagName === "BR") {
    return "";
  }

  return Array.from(element.childNodes).map(serializeSubjectNode).join("");
};

const serializeSubjectEditor = (editor: HTMLElement) =>
  Array.from(editor.childNodes).map(serializeSubjectNode).join("");

const getSelectionRangeInEditor = (editor: HTMLElement) => {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) {
    return null;
  }

  const range = selection.getRangeAt(0);
  if (!editor.contains(range.commonAncestorContainer)) {
    return null;
  }

  return range.cloneRange();
};

const placeCaretAfterNode = (node: Node) => {
  const range = document.createRange();
  range.setStartAfter(node);
  range.collapse(true);

  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);

  return range.cloneRange();
};

export const SubjectTemplateInput = ({
  label,
  value,
  onChange,
  placeholder,
  required = false,
  disabled = false,
  className = "block",
  inputClassName = "min-h-10 w-full rounded-xl border border-stone-200 bg-white px-4 py-2 pr-28 text-sm leading-6 text-stone-700 outline-none transition-all hover:border-stone-300 focus:border-primary focus:ring-2 focus:ring-primary/20",
}: SubjectTemplateInputProps) => {
  const labelId = useId();
  const [open, setOpen] = useState(false);
  const editorRef = useRef<HTMLDivElement | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement | null>(null);
  const lastRangeRef = useRef<Range | null>(null);

  const saveSelectionRange = () => {
    if (disabled) {
      return;
    }
    const editor = editorRef.current;
    if (!editor) {
      return;
    }

    const range = getSelectionRangeInEditor(editor);
    if (range) {
      lastRangeRef.current = range;
    }
  };

  useLayoutEffect(() => {
    const editor = editorRef.current;
    if (!editor || serializeSubjectEditor(editor) === value) {
      return;
    }

    renderSubjectValue(editor, value);
  }, [value]);

  const getInsertionRange = (editor: HTMLElement) => {
    const savedRange = lastRangeRef.current;
    if (savedRange && editor.contains(savedRange.commonAncestorContainer)) {
      return savedRange.cloneRange();
    }

    const currentRange = getSelectionRangeInEditor(editor);
    if (currentRange) {
      return currentRange;
    }

    const fallbackRange = document.createRange();
    fallbackRange.selectNodeContents(editor);
    fallbackRange.collapse(false);
    return fallbackRange;
  };

  const emitEditorValue = () => {
    if (disabled) {
      return;
    }
    const editor = editorRef.current;
    if (!editor) {
      return;
    }

    onChange(serializeSubjectEditor(editor));
    saveSelectionRange();
  };

  const insertPlaceholder = (option: TemplatePlaceholderOption) => {
    if (disabled) {
      return;
    }
    const editor = editorRef.current;
    if (!editor) {
      return;
    }

    const range = getInsertionRange(editor);
    const chip = createPlaceholderChip(option);

    editor.focus();
    range.deleteContents();
    range.insertNode(chip);
    lastRangeRef.current = placeCaretAfterNode(chip);
    onChange(serializeSubjectEditor(editor));
    setOpen(false);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (disabled) {
      event.preventDefault();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
    }
  };

  const handlePaste = (event: ClipboardEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (disabled) {
      return;
    }

    const editor = editorRef.current;
    if (!editor) {
      return;
    }

    const text = event.clipboardData.getData("text/plain");
    if (!text) {
      return;
    }

    const range = getInsertionRange(editor);
    const textNode = document.createTextNode(text);
    range.deleteContents();
    range.insertNode(textNode);
    lastRangeRef.current = placeCaretAfterNode(textNode);
    onChange(serializeSubjectEditor(editor));
  };

  return (
    <div className={className}>
      <label
        id={labelId}
        className="mb-2 inline-flex items-center gap-1 text-sm font-medium text-stone-800"
        onClick={() => editorRef.current?.focus()}
      >
        {required ? <span className="text-base leading-none text-red-500">*</span> : null}
        <span>{label}</span>
      </label>
      <div className="relative">
        <div
          ref={editorRef}
          role="textbox"
          aria-labelledby={labelId}
          aria-required={required}
          aria-multiline={false}
          aria-disabled={disabled}
          contentEditable={!disabled}
          suppressContentEditableWarning
          onFocus={saveSelectionRange}
          onMouseUp={saveSelectionRange}
          onKeyUp={saveSelectionRange}
          onKeyDown={handleKeyDown}
          onInput={emitEditorValue}
          onPaste={handlePaste}
          className={`${inputClassName} ${disabled ? "cursor-not-allowed opacity-60" : "cursor-text"} whitespace-pre-wrap break-words selection:bg-primary/15`}
        />
        {!value && placeholder ? (
          <span className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-sm text-stone-400">
            {placeholder}
          </span>
        ) : null}

        <div className="absolute right-1.5 top-1/2 -translate-y-1/2">
          <button
            ref={menuButtonRef}
            type="button"
            aria-label="主题占位符菜单"
            aria-expanded={open}
            disabled={disabled}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => {
              if (!disabled) {
                setOpen((current) => !current);
              }
            }}
            className="inline-flex h-8 items-center gap-1 rounded-lg border border-stone-200 bg-white px-2 text-xs font-medium text-stone-600 transition hover:border-stone-300 hover:bg-stone-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Braces className="h-3.5 w-3.5" />
            占位符
            <ChevronDown className="h-3.5 w-3.5 text-stone-400" />
          </button>

          <FloatingMenuPortal
            open={open}
            anchorRef={menuButtonRef}
            align="right"
            minWidth={180}
            testId="subject-placeholder-menu"
            onClose={() => setOpen(false)}
          >
            {TEMPLATE_PLACEHOLDER_OPTIONS.map((option) => (
              <button
                key={option.key}
                type="button"
                aria-label={option.label}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => insertPlaceholder(option)}
                className="flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left text-sm text-stone-700 transition hover:bg-stone-50"
              >
                <span>{option.label}</span>
                <span className="font-mono text-xs text-stone-400">{option.token}</span>
              </button>
            ))}
          </FloatingMenuPortal>
        </div>
      </div>
    </div>
  );
};
