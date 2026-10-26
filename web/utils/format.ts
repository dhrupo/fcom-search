export function postedAgo(seconds: number | null | undefined, now = Date.now() / 1000) {
  if (!seconds) return null;
  const days = Math.floor((now - seconds) / 86400);
  return days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`;
}

export const CITY_STATUS: Record<string, (found: number) => string> = {
  searching: () => "searching…",
  ok: (found) => `${found} found`,
  blocked: () => "Facebook is slowing us down; try again later",
  failed: () => "could not be searched",
  no_address: () => "has no Marketplace address",
};

export const PROBLEMS: Record<string, string> = {
  "": "Search failed. Please check the place and try again.",
  human: "We could not confirm you are human. Wait a moment and search again.",
  slow: "Slow down, try again in a minute.",
};
