/**
 * Shared visual baseline for full-screen, centered dialogs.
 *
 * Drawers and anchored popovers intentionally keep their own layout. Centered
 * dialogs compose these classes with their own width and scrolling rules.
 */
export const MODAL_BACKDROP_CLASS_NAME =
  "fixed inset-0 flex items-center justify-center bg-stone-950/35 p-4 backdrop-blur-md";

export const MODAL_SURFACE_CLASS_NAME =
  "relative overflow-hidden rounded-[30px] border border-stone-200/80 bg-[linear-gradient(180deg,rgba(255,252,246,0.98),rgba(255,245,233,0.95))] shadow-[0_34px_90px_-32px_rgba(41,37,36,0.5)]";

