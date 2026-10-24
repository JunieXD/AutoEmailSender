import { call } from "@/api/call";

/** Uploads the same material from settings or while composing a letter. */
export async function uploadMaterial(file: File) {
  // material::MAX_SIZE: refuse before reading and encoding a large file in memory.
  if (file.size > 20 * 1024 * 1024) throw new Error("文件超过 20 MB，无法添加。");
  return call("addMaterial", { fileName: file.name, data: await base64(file) });
}

/** The file's bytes as base64, which JSON can carry. */
async function base64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  // In slices, as a call takes only so many arguments.
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}
