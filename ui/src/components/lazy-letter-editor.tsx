import { type ComponentProps, lazy, Suspense } from "react";
import type { LetterEditor as Editor } from "@/components/letter-editor";

// The editor is most of the interface's code; loaded when first needed, it does not slow the start.
const LetterEditor = lazy(() => import("@/components/letter-editor").then((m) => ({ default: m.LetterEditor })));

/** `LetterEditor`, loaded the first time one is shown. */
export function LazyLetterEditor(props: ComponentProps<typeof Editor>) {
  return (
    <Suspense fallback={<div className="h-[236px] rounded-md bg-muted" />}>
      <LetterEditor {...props} />
    </Suspense>
  );
}
