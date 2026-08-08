export type PreventableNavigationEvent = {
  preventDefault: () => void;
};

export function isProtectedBackendNavigation(
  targetUrl: unknown,
  backendBaseUrl: unknown,
): boolean {
  if (typeof targetUrl !== "string" || typeof backendBaseUrl !== "string") {
    return false;
  }

  try {
    const target = new URL(targetUrl);
    const backend = new URL(backendBaseUrl);
    return (
      target.origin === backend.origin
      && (target.pathname === "/api" || target.pathname.startsWith("/api/"))
    );
  } catch {
    return false;
  }
}

export function preventProtectedBackendNavigation(
  event: PreventableNavigationEvent,
  targetUrl: unknown,
  backendBaseUrl: unknown,
): boolean {
  if (!isProtectedBackendNavigation(targetUrl, backendBaseUrl)) {
    return false;
  }
  event.preventDefault();
  return true;
}
