export const normalizeExternalHttpUrl = (url: string | null | undefined) => {
  const normalizedUrl = url?.trim();
  if (!normalizedUrl) {
    return null;
  }

  try {
    const parsedUrl = new URL(normalizedUrl);
    return parsedUrl.protocol === "http:" || parsedUrl.protocol === "https:"
      ? normalizedUrl
      : null;
  } catch {
    return null;
  }
};

export const openExternalHttpUrl = (url: string) => {
  const normalizedUrl = normalizeExternalHttpUrl(url);
  if (!normalizedUrl) {
    return;
  }

  if (!window.autoEmailSender?.openExternalUrl) {
    window.open(normalizedUrl, "_blank", "noopener,noreferrer");
    return;
  }

  void window.autoEmailSender.openExternalUrl(normalizedUrl).catch(() => {
    window.open(normalizedUrl, "_blank", "noopener,noreferrer");
  });
};
