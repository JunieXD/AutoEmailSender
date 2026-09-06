import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { lstat, readdir, readFile, readlink } from "node:fs/promises";
import path from "node:path";

export async function sha256File(targetPath: string): Promise<string> {
  const hash = createHash("sha256");
  await new Promise<void>((resolve, reject) => {
    const stream = createReadStream(targetPath);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.on("error", reject);
    stream.on("end", resolve);
  });
  return hash.digest("hex");
}

export async function sha256Directory(directoryPath: string): Promise<string> {
  const entries: string[] = [];
  const visit = async (relativeDirectory: string): Promise<void> => {
    const absoluteDirectory = relativeDirectory
      ? path.join(directoryPath, ...relativeDirectory.split("/"))
      : directoryPath;
    const children = await readdir(absoluteDirectory, { withFileTypes: true });
    children.sort((left, right) => compareOrdinal(left.name, right.name));
    for (const child of children) {
      const relativePath = relativeDirectory
        ? `${relativeDirectory}/${child.name}`
        : child.name;
      const absolutePath = path.join(absoluteDirectory, child.name);
      if (child.isDirectory()) {
        entries.push(`D\t${relativePath}`);
        await visit(relativePath);
      } else if (child.isFile()) {
        entries.push(`F\t${relativePath}\t${await sha256File(absolutePath)}`);
      } else if (child.isSymbolicLink()) {
        entries.push(`L\t${relativePath}\t${await readlink(absolutePath)}`);
      } else {
        entries.push(`O\t${relativePath}`);
      }
    }
  };
  await visit("");
  const canonicalListing = entries.length > 0 ? `${entries.join("\n")}\n` : "";
  return createHash("sha256").update(canonicalListing, "utf8").digest("hex");
}

export async function fileTextMatches(
  targetPath: string,
  expected: string,
): Promise<boolean> {
  try {
    return (await readFile(targetPath, "utf8")) === expected;
  } catch {
    return false;
  }
}

export async function directoryFingerprintMatches(
  targetPath: string,
  expected: string,
): Promise<boolean> {
  try {
    return (
      (await lstat(targetPath)).isDirectory() &&
      (await sha256Directory(targetPath)) === expected
    );
  } catch {
    return false;
  }
}

export function compareOrdinal(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

export function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
}
