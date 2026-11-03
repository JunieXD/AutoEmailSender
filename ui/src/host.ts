/**
 * Whether the page runs in the desktop app, not on a phone through the relay, where what only the
 * computer does is not shown: files saved there, its updates and settings (docs/ui.md).
 */
export const desktop = import.meta.env.MODE !== "phone";
