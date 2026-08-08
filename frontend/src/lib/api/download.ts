import { apiFetchBlob } from "@/lib/api/client";

type ApiQueryParams = Record<string, string | number | null | undefined>;

export const fetchApiFile = (
  path: string,
  options?: RequestInit,
  params?: ApiQueryParams,
): Promise<Blob> => apiFetchBlob(path, options, params);

export async function downloadApiFile(
  path: string,
  filename: string,
  options?: RequestInit,
  params?: ApiQueryParams,
): Promise<void> {
  downloadBlob(await fetchApiFile(path, options, params), filename);
}

export function downloadBlob(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}
