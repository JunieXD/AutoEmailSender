import type { KeyboardEvent } from "react";

/** A key pressed while an input method composes, such as Enter picking Chinese characters, is its own. */
export function composing(e: KeyboardEvent) {
  // WebKit ends a composition with a keydown whose `isComposing` is already false, but whose code is 229.
  return e.nativeEvent.isComposing || e.keyCode === 229;
}
