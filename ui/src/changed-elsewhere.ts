import type { MutationState } from "@tanstack/react-query";
import { useState } from "react";

/**
 * What is stored, when it was changed elsewhere, such as by an Agent, since this page last saw it,
 * and is to be shown in place of what is being edited. Text not saved yet stays. The receipt of
 * this page's last save, which may differ in whitespace from what was typed, is its own the one
 * time it comes in; the same text stored again later is a change like any other.
 *
 * The component calling this reads `stored` from its query rather than being passed it, so that a
 * render sees it and the saves at the same moment. It is undefined while being read again.
 */
export function useChangedElsewhere<T>(
  stored: T | undefined,
  last: MutationState<T> | undefined,
  same: (a: T, b: T) => boolean,
): T | undefined {
  const [seen, setSeen] = useState(stored);
  const [counted, setCounted] = useState(last);
  if (stored === undefined || (seen !== undefined && same(stored, seen))) return undefined;
  if (last && last.status !== "success") return undefined;
  setSeen(stored);
  setCounted(last);
  return last && last !== counted && last.data !== undefined && same(stored, last.data) ? undefined : stored;
}
