import { call } from "@/api/call";
import { base64, MAX_UPLOAD } from "@/file";

/** Uploads the same material from settings or while composing a letter. */
export async function uploadMaterial(file: File) {
  if (file.size > MAX_UPLOAD) throw new Error("文件超过 20 MB，无法添加。");
  return call("addMaterial", { fileName: file.name, data: await base64(file) });
}
