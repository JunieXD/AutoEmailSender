import { call } from "@/api/call";
import { base64 } from "@/file";

/** Uploads the same material from settings or while composing a letter. */
export async function uploadMaterial(file: File) {
  // material::MAX_SIZE: refuse before reading and encoding a large file in memory.
  if (file.size > 20 * 1024 * 1024) throw new Error("文件超过 20 MB，无法添加。");
  return call("addMaterial", { fileName: file.name, data: await base64(file) });
}
