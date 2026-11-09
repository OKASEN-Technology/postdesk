import type { Status } from "./api";

export const STATUS_STYLE: Record<Status, string> = {
  draft: "bg-paper-2 text-ink-2",
  approved: "bg-moss-2 text-moss",
  publishing: "bg-sun-2 text-sun",
  published: "bg-ink text-paper",
  failed: "bg-clay-2 text-clay",
  rejected: "bg-paper-2 text-ink-3",
};

export const STATUS_LABEL: Record<Status, string> = {
  draft: "to review",
  approved: "scheduled",
  publishing: "publishing",
  published: "published",
  failed: "failed",
  rejected: "rejected",
};

export function Badge({ status }: { status: Status }) {
  return <span className={`w-fit rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[status]}`}>{STATUS_LABEL[status]}</span>;
}

export const CARD = "rounded-lg border border-line bg-white p-4";
export const LABEL = "text-xs font-medium uppercase tracking-wide text-ink-3";
export const INPUT = "mt-1 w-full rounded border border-line bg-white p-2 text-sm text-ink focus:border-moss focus:outline-none";
export const PRIMARY = "rounded bg-moss px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50";
export const SECONDARY = "rounded border border-ink px-4 py-2 text-sm font-medium text-ink hover:bg-paper-2 disabled:opacity-50";
export const DANGER = "rounded px-4 py-2 text-sm font-medium text-clay hover:underline disabled:opacity-50";

export function ErrorNote({ text }: { text: string | null }) {
  if (!text) return null;
  return <p className="rounded bg-clay-2 p-2 text-sm text-clay whitespace-pre-wrap">{text}</p>;
}
