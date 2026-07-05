// Small, dependency-free date helpers for the custom DatePicker.
// All public values are ISO `YYYY-MM-DD` strings parsed/formatted in LOCAL time
// (no UTC shift), so the day a user clicks is the day that gets stored.

export const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'] as const

export const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
] as const

/** Format a Date as local-time ISO `YYYY-MM-DD`. */
export function toISO(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** Parse an ISO `YYYY-MM-DD` string into a local-time Date, or null if invalid. */
export function fromISO(iso: string | undefined | null): Date | null {
  if (!iso) return null
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  if (!m) return null
  const y = Number(m[1])
  const mo = Number(m[2]) - 1
  const d = Number(m[3])
  const date = new Date(y, mo, d)
  // guard against rollover (e.g. 2026-02-31)
  if (date.getFullYear() !== y || date.getMonth() !== mo || date.getDate() !== d) return null
  return date
}

/** Today as local-time ISO. */
export function todayISO(): string {
  return toISO(new Date())
}

export function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

/** Number of days in a given month (0-indexed month). */
export function daysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate()
}

/** Weekday index (0=Sun) of the first day of a month. */
export function firstWeekday(year: number, month: number): number {
  return new Date(year, month, 1).getDay()
}

/**
 * Build the 6x7 grid of Dates for a month view, including leading/trailing
 * days from adjacent months so every cell is filled.
 */
export function monthGrid(year: number, month: number): Date[] {
  const lead = firstWeekday(year, month)
  const start = new Date(year, month, 1 - lead)
  const cells: Date[] = []
  for (let i = 0; i < 42; i++) {
    cells.push(new Date(start.getFullYear(), start.getMonth(), start.getDate() + i))
  }
  return cells
}

/** Inclusive bounds check against optional ISO min/max. */
export function isInRange(d: Date, min?: string | null, max?: string | null): boolean {
  const iso = toISO(d)
  if (min && iso < min) return false
  if (max && iso > max) return false
  return true
}

/** Add days to a Date (returns a new Date). */
export function addDays(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n)
}

/** Human-friendly display of an ISO date, e.g. "Jun 28, 2026"; empty string if blank. */
export function displayISO(iso: string | undefined | null): string {
  const d = fromISO(iso)
  if (!d) return ''
  return `${MONTHS[d.getMonth()].slice(0, 3)} ${d.getDate()}, ${d.getFullYear()}`
}
