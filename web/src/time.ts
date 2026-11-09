/** Times are shown and picked in the account's timezone (postdesk.toml
 * `[account].timezone`), whatever the browser's is, so what you see is
 * what the publisher uses. */

const pad = (n: number) => String(n).padStart(2, "0");

export function fmt(iso: string | null | undefined, tz: string): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: tz,
  }).format(new Date(iso));
}

/** The wall-clock parts of an instant in `tz`. */
export function wallParts(d: Date, tz: string) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).formatToParts(d);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  return { year: get("year"), month: get("month"), day: get("day"), hour: get("hour"), minute: get("minute") };
}

/** An ISO instant -> the value for a `datetime-local` input, in `tz`. */
export function toLocalInput(iso: string, tz: string): string {
  const w = wallParts(new Date(iso), tz);
  return `${w.year}-${pad(w.month)}-${pad(w.day)}T${pad(w.hour)}:${pad(w.minute)}`;
}

/** A `datetime-local` value, read as wall time in `tz` -> a UTC instant. */
export function fromLocalInput(value: string, tz: string): Date {
  const [date, time] = value.split("T");
  const [y, m, d] = date.split("-").map(Number);
  const [hh, mm] = time.split(":").map(Number);
  // Guess the instant as if UTC, measure the zone's offset there, correct, and
  // measure once more (the offset can change across a DST edge).
  let guess = Date.UTC(y, m - 1, d, hh, mm);
  for (let i = 0; i < 2; i++) {
    const w = wallParts(new Date(guess), tz);
    const asUtc = Date.UTC(w.year, w.month - 1, w.day, w.hour, w.minute);
    guess += Date.UTC(y, m - 1, d, hh, mm) - asUtc;
  }
  return new Date(guess);
}

export function hhmm(value: string): string {
  return value.split("T")[1] ?? "";
}

/** Whether a `datetime-local` wall time falls inside the publisher's hours. */
export function insidePublisherHours(value: string, hours: [string, string]): boolean {
  const t = hhmm(value);
  return t >= hours[0] && t <= hours[1];
}
