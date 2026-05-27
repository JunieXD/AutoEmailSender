import Blockquote from "@tiptap/extension-blockquote";
import Bold from "@tiptap/extension-bold";
import BulletList from "@tiptap/extension-bullet-list";
import Code from "@tiptap/extension-code";
import CodeBlock from "@tiptap/extension-code-block";
import Heading from "@tiptap/extension-heading";
import HorizontalRule from "@tiptap/extension-horizontal-rule";
import Italic from "@tiptap/extension-italic";
import OrderedList from "@tiptap/extension-ordered-list";

export const BoldWithoutInputRules = Bold.extend({
  addInputRules() {
    return [];
  },
});

export const ItalicWithoutInputRules = Italic.extend({
  addInputRules() {
    return [];
  },
});

export const CodeWithoutInputRules = Code.extend({
  addInputRules() {
    return [];
  },
});

export const HeadingWithoutInputRules = Heading.extend({
  addInputRules() {
    return [];
  },
});

export const BlockquoteWithoutInputRules = Blockquote.extend({
  addInputRules() {
    return [];
  },
});

export const CodeBlockWithoutInputRules = CodeBlock.extend({
  addInputRules() {
    return [];
  },
});

export const HorizontalRuleWithoutInputRules = HorizontalRule.extend({
  addInputRules() {
    return [];
  },
});

export const OrderedListWithoutInputRules = OrderedList.extend({
  addInputRules() {
    return [];
  },
});

export const BulletListWithoutInputRules = BulletList.extend({
  addInputRules() {
    return [];
  },
});

const inputRuleCount = (addInputRules: unknown) =>
  typeof addInputRules === "function" ? (addInputRules as () => unknown[])().length : undefined;

export const emailTemplateEditorDisablesStructuralInputRules = () =>
  inputRuleCount(HeadingWithoutInputRules.config.addInputRules) === 0 &&
  inputRuleCount(BlockquoteWithoutInputRules.config.addInputRules) === 0 &&
  inputRuleCount(CodeBlockWithoutInputRules.config.addInputRules) === 0 &&
  inputRuleCount(HorizontalRuleWithoutInputRules.config.addInputRules) === 0 &&
  inputRuleCount(OrderedListWithoutInputRules.config.addInputRules) === 0 &&
  inputRuleCount(BulletListWithoutInputRules.config.addInputRules) === 0;

export const emailTemplateEditorDisablesInlineMarkdownInputRules = () =>
  inputRuleCount(BoldWithoutInputRules.config.addInputRules) === 0 &&
  inputRuleCount(ItalicWithoutInputRules.config.addInputRules) === 0 &&
  inputRuleCount(CodeWithoutInputRules.config.addInputRules) === 0;