import { Node, mergeAttributes } from "@tiptap/core";
import {
  getTemplatePlaceholder,
  type TemplatePlaceholderKey,
} from "@/lib/templatePlaceholders";

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    templatePlaceholder: {
      insertTemplatePlaceholder: (key: TemplatePlaceholderKey) => ReturnType;
    };
  }
}

export const TemplatePlaceholder = Node.create({
  name: "templatePlaceholder",
  inline: true,
  group: "inline",
  atom: true,
  selectable: true,

  addAttributes() {
    return {
      key: {
        default: null,
        parseHTML: (element) => element.getAttribute("data-template-placeholder"),
        renderHTML: (attributes) => ({
          "data-template-placeholder": attributes.key,
        }),
      },
    };
  },

  parseHTML() {
    return [{ tag: "span[data-template-placeholder]" }];
  },

  renderHTML({ node, HTMLAttributes }) {
    const option = getTemplatePlaceholder(node.attrs.key);
    return [
      "span",
      mergeAttributes(HTMLAttributes, {
        class: "email-placeholder-chip",
        "data-token": option?.token ?? "",
      }),
      option?.label ?? node.attrs.key,
    ];
  },

  renderText({ node }) {
    return getTemplatePlaceholder(node.attrs.key)?.token ?? "";
  },

  addCommands() {
    return {
      insertTemplatePlaceholder:
        (key) =>
        ({ commands, state }) => {
          const activeMarks = state.storedMarks ?? state.selection.$from.marks();
          return commands.insertContent({
            type: this.name,
            attrs: { key },
            marks: activeMarks.map((mark) => mark.toJSON()),
          });
        },
    };
  },
});
