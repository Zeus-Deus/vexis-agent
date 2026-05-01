// Bearer-token plumbing for the dashboard.
//
// The token arrives one of three ways:
//   1. ?token=<value> on first load (handed out by /dashboard in Telegram)
//   2. localStorage carryover from a prior visit
//   3. nothing — the user lands on /no-token and is asked to /dashboard
//
// On case (1) we stash it in localStorage and rewrite the URL via
// history.replaceState so the token doesn't sit in the address bar
// (and doesn't end up in the user's browser history).

const STORAGE_KEY = "vexis.dashboardToken";

export function bootstrapToken(): string | null {
  const url = new URL(window.location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    try {
      window.localStorage.setItem(STORAGE_KEY, fromUrl);
    } catch {
      // Private mode / quota — fall through; the in-memory token below
      // will at least let the current tab work.
    }
    url.searchParams.delete("token");
    window.history.replaceState({}, "", url.toString());
    return fromUrl;
  }
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function clearToken(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}
