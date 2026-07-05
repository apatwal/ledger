import { useEffect, useRef, useState, useCallback, useLayoutEffect } from 'react'
import { Calendar, ChevronLeft, ChevronRight } from 'lucide-react'
import {
  WEEKDAYS,
  MONTHS,
  toISO,
  fromISO,
  todayISO,
  sameDay,
  monthGrid,
  isInRange,
  addDays,
  displayISO,
} from '../lib/date'

interface Props {
  /** ISO YYYY-MM-DD */
  value: string
  onChange: (iso: string) => void
  /** Inclusive ISO bounds; days outside are disabled. */
  min?: string
  max?: string
  /** Accessible label for the trigger (e.g. "From date"). */
  ariaLabel?: string
  /** Visual width variant: inline (auto) for filter bars, block (100%) for grids. */
  block?: boolean
  id?: string
  placeholder?: string
}

// A range of years for the quick year jump dropdown.
const YEAR_SPAN = 8

export default function DatePicker({
  value,
  onChange,
  min,
  max,
  ariaLabel,
  block = false,
  id,
  placeholder = 'Select date',
}: Props) {
  const [open, setOpen] = useState(false)
  const selected = fromISO(value)
  const today = fromISO(todayISO())!

  // The month currently shown in the popover.
  const [viewYear, setViewYear] = useState((selected ?? today).getFullYear())
  const [viewMonth, setViewMonth] = useState((selected ?? today).getMonth())
  // Keyboard focus cursor (the day the arrow keys move).
  const [cursor, setCursor] = useState<Date>(selected ?? today)

  const rootRef = useRef<HTMLDivElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const [popAbove, setPopAbove] = useState(false)

  // When opening, re-sync the visible month + cursor to the current value.
  useEffect(() => {
    if (open) {
      const base = fromISO(value) ?? today
      setViewYear(base.getFullYear())
      setViewMonth(base.getMonth())
      setCursor(base)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // Flip the popover above the trigger if there isn't room below.
  useLayoutEffect(() => {
    if (open && triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect()
      const below = window.innerHeight - rect.bottom
      setPopAbove(below < 360)
    }
  }, [open])

  // Outside-click + Esc to close.
  useEffect(() => {
    if (!open) return
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setOpen(false)
        triggerRef.current?.focus()
      }
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Keep the grid focused so arrow keys work as soon as it opens.
  useEffect(() => {
    if (open) popRef.current?.focus()
  }, [open])

  const commit = useCallback(
    (d: Date) => {
      if (!isInRange(d, min, max)) return
      onChange(toISO(d))
      setOpen(false)
      triggerRef.current?.focus()
    },
    [min, max, onChange]
  )

  function goMonth(delta: number) {
    let m = viewMonth + delta
    let y = viewYear
    if (m < 0) { m = 11; y -= 1 }
    if (m > 11) { m = 0; y += 1 }
    setViewMonth(m)
    setViewYear(y)
  }

  function onGridKeyDown(e: React.KeyboardEvent) {
    let next: Date | null = null
    switch (e.key) {
      case 'ArrowLeft': next = addDays(cursor, -1); break
      case 'ArrowRight': next = addDays(cursor, 1); break
      case 'ArrowUp': next = addDays(cursor, -7); break
      case 'ArrowDown': next = addDays(cursor, 7); break
      case 'PageUp': next = new Date(cursor.getFullYear(), cursor.getMonth() - 1, cursor.getDate()); break
      case 'PageDown': next = new Date(cursor.getFullYear(), cursor.getMonth() + 1, cursor.getDate()); break
      case 'Home': next = new Date(cursor.getFullYear(), cursor.getMonth(), 1); break
      case 'End': next = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0); break
      case 'Enter':
      case ' ':
        e.preventDefault()
        commit(cursor)
        return
      default:
        return
    }
    if (next) {
      e.preventDefault()
      setCursor(next)
      setViewYear(next.getFullYear())
      setViewMonth(next.getMonth())
    }
  }

  const grid = monthGrid(viewYear, viewMonth)
  const years: number[] = []
  for (let y = today.getFullYear() - YEAR_SPAN; y <= today.getFullYear() + 1; y++) years.push(y)

  return (
    <div className="dp-root" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        id={id}
        className={`date-input dp-trigger${block ? ' date-input-block' : ''}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={() => setOpen((o) => !o)}
      >
        <span className={value ? 'dp-value' : 'dp-placeholder'}>
          {value ? displayISO(value) : placeholder}
        </span>
        <Calendar size={14} className="dp-trigger-icon" aria-hidden="true" />
      </button>

      {open && (
        <div
          ref={popRef}
          className={`dp-popover${popAbove ? ' above' : ''}`}
          role="dialog"
          aria-modal="false"
          aria-label={ariaLabel ?? 'Choose date'}
          tabIndex={-1}
          onKeyDown={onGridKeyDown}
        >
          <div className="dp-header">
            <button
              type="button"
              className="dp-nav"
              aria-label="Previous month"
              onClick={() => goMonth(-1)}
            >
              <ChevronLeft size={15} />
            </button>

            <div className="dp-selects">
              <select
                className="dp-select"
                aria-label="Month"
                value={viewMonth}
                onChange={(e) => setViewMonth(Number(e.target.value))}
              >
                {MONTHS.map((mName, i) => (
                  <option key={mName} value={i}>{mName}</option>
                ))}
              </select>
              <select
                className="dp-select"
                aria-label="Year"
                value={viewYear}
                onChange={(e) => setViewYear(Number(e.target.value))}
              >
                {years.map((y) => (
                  <option key={y} value={y}>{y}</option>
                ))}
              </select>
            </div>

            <button
              type="button"
              className="dp-nav"
              aria-label="Next month"
              onClick={() => goMonth(1)}
            >
              <ChevronRight size={15} />
            </button>
          </div>

          <div className="dp-weekdays" aria-hidden="true">
            {WEEKDAYS.map((w) => (
              <span key={w} className="dp-weekday">{w}</span>
            ))}
          </div>

          <div className="dp-grid" role="grid">
            {grid.map((d) => {
              const inMonth = d.getMonth() === viewMonth
              const isSelected = selected ? sameDay(d, selected) : false
              const isToday = sameDay(d, today)
              const isCursor = sameDay(d, cursor)
              const disabled = !isInRange(d, min, max)
              const cls = [
                'dp-day',
                inMonth ? '' : 'outside',
                isSelected ? 'selected' : '',
                isToday ? 'today' : '',
                isCursor ? 'cursor' : '',
                disabled ? 'disabled' : '',
              ].filter(Boolean).join(' ')
              return (
                <button
                  key={toISO(d)}
                  type="button"
                  role="gridcell"
                  aria-selected={isSelected}
                  aria-current={isToday ? 'date' : undefined}
                  aria-label={`${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`}
                  className={cls}
                  disabled={disabled}
                  tabIndex={-1}
                  onClick={() => commit(d)}
                  onMouseEnter={() => setCursor(d)}
                >
                  {d.getDate()}
                </button>
              )
            })}
          </div>

          <div className="dp-footer">
            <button
              type="button"
              className="dp-today-btn"
              disabled={!isInRange(today, min, max)}
              onClick={() => commit(today)}
            >
              Today
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
