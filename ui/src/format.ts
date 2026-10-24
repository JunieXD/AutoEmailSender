/** "今天", "昨天", or the date, in the user's time zone (docs/ui.md#文案). */
export function day(ms: number): string {
  const date = new Date(ms);
  const today = new Date();
  const days = Math.round((startOfDay(today) - startOfDay(date)) / 86_400_000);
  if (days === 0) return "今天";
  if (days === 1) return "昨天";
  const md = `${date.getMonth() + 1}月${date.getDate()}日`;
  return date.getFullYear() === today.getFullYear() ? md : `${date.getFullYear()}年${md}`;
}

/** "320 KB", "1.2 MB". */
export function fileSize(bytes: number): string {
  const kb = bytes / 1024;
  return kb < 1024 ? `${Math.max(1, Math.round(kb))} KB` : `${(kb / 1024).toFixed(1)} MB`;
}

/** "09:30", in the user's time zone. */
export function time(ms: number): string {
  const date = new Date(ms);
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}
