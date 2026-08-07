export const formatFileSize = (sizeBytes: number) => {
  const normalizedBytes = Number.isFinite(sizeBytes) ? Math.max(0, sizeBytes) : 0;
  if (normalizedBytes < 1024) {
    return `${Math.round(normalizedBytes)} B`;
  }
  if (normalizedBytes < 1024 * 1024) {
    return `${(normalizedBytes / 1024).toFixed(1)} KB`;
  }
  return `${(normalizedBytes / (1024 * 1024)).toFixed(2)} MB`;
};
