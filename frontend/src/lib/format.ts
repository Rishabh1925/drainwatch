// Render helpers shared across pages. The backend sends ISO-8601 UTC timestamps; the
// interface shows them in IST, which is the operating timezone of the Delhi pilot.

const IST = "Asia/Kolkata";

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const d = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: IST }).format(date);
  const t = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: IST }).format(date);
  return `${d} · ${t} IST`;
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: IST }).format(date)} IST`;
}

export function fmtPct(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(1)}%`;
}

export function fmtSla(sla: { remaining_minutes: number | null; state: string } | undefined): string {
  if (!sla || sla.remaining_minutes === null) return "No SLA";
  const minutes = Math.abs(sla.remaining_minutes);
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  const clock = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  return sla.remaining_minutes >= 0 ? `${clock} remaining` : `Overdue ${clock}`;
}
