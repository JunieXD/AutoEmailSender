import Blockquote from "@tiptap/extension-blockquote";
import BulletList from "@tiptap/extension-bullet-list";
import CodeBlock from "@tiptap/extension-code-block";
import Heading from "@tiptap/extension-heading";
import HorizontalRule from "@tiptap/extension-horizontal-rule";
import OrderedList from "@tiptap/extension-ordered-list";

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
