import { useEffect, useId, useRef, useState } from 'react'
import { AlertCircle, Check, ChevronRight, Copy, RotateCw, X } from 'lucide-react'
import { ApiError, statusLabel } from '../lib/api'

// ─── ErrorNotice ──────────────────────────────────────────────────────────────
// The one way a load/action failure is shown: a plain-English headline ("Couldn't
// load transactions"), a one-line explanation, and — for API errors — a collapsed
// Details block with the request, status, real cause and server error ID, plus a
// Copy button. Nothing is persisted; the error ID is what links this to the full
// stack trace in the server (Render) logs.
//
// `error` may be an ApiError (full details), any other Error (message only), or a
// plain string (e.g. a sync-failure note) — the latter two render without Details.

/** A failed action held in component state: its headline + whatever was thrown. */
export interface FailedAction {
  title: string
  error: unknown
}

interface Props {
  /** Short headline, e.g. "Couldn't load transactions". */
  title: string
  error: unknown
  onRetry?: () => void
  onDismiss?: () => void
  /** Tighter padding, no shadow — for modals and side panels. */
  compact?: boolean
  className?: string
}

interface DetailRow {
  label: string
  value: string
}

function explain(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.isNetwork) return error.message
    if (error.isServerCrash) return 'Something broke on the server while handling this request.'
    return error.message
  }
  if (error instanceof Error) return error.message || 'Something went wrong.'
  if (typeof error === 'string' && error.trim()) return error
  return 'Something went wrong.'
}

function detailRows(error: ApiError, explanation: string): DetailRow[] {
  const rows: DetailRow[] = [
    { label: 'Request', value: `${error.method} ${error.path}` },
    { label: 'Status', value: error.status === null ? 'No response' : statusLabel(error.status) },
  ]
  // Skip a cause that only repeats the explanation line (typical for a 4xx).
  if (error.cause && error.cause !== explanation) rows.push({ label: 'Cause', value: error.cause })
  if (error.errorId) rows.push({ label: 'Error ID', value: error.errorId })
  rows.push({ label: 'Time', value: error.at.toLocaleString() })
  return rows
}

function copyText(title: string, explanation: string, error: ApiError, rows: DetailRow[]): string {
  const width = Math.max(...rows.map((r) => r.label.length)) + 2
  const lines = rows.map((r) =>
    `${`${r.label}:`.padEnd(width)}${r.label === 'Time' ? error.at.toISOString() : r.value}`,
  )
  return [title, explanation, '', ...lines].join('\n')
}

async function writeClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Clipboard API is unavailable outside a secure context — fall back.
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.setAttribute('readonly', '')
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      return ok
    } catch {
      return false
    }
  }
}

export default function ErrorNotice({ title, error, onRetry, onDismiss, compact, className }: Props) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState<'ok' | 'failed' | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const panelId = useId()

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const explanation = explain(error)
  const rows = error instanceof ApiError ? detailRows(error, explanation) : null

  async function handleCopy() {
    if (!(error instanceof ApiError) || !rows) return
    const ok = await writeClipboard(copyText(title, explanation, error, rows))
    setCopied(ok ? 'ok' : 'failed')
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setCopied(null), 1600)
  }

  const classes = ['error-notice', compact ? 'error-notice-compact' : '', className ?? '']
    .filter(Boolean)
    .join(' ')

  return (
    <div className={classes} role="alert">
      <AlertCircle size={compact ? 16 : 18} className="error-notice-icon" aria-hidden="true" />
      <div className="error-notice-body">
        <div className="error-notice-title">{title}</div>
        <div className="error-notice-text">{explanation}</div>

        {(rows || onRetry) && (
          <div className="error-notice-actions">
            {onRetry && (
              <button type="button" className="btn btn-secondary btn-sm" onClick={onRetry}>
                <RotateCw size={13} />
                Retry
              </button>
            )}
            {rows && (
              <button
                type="button"
                className="error-notice-toggle"
                aria-expanded={open}
                aria-controls={panelId}
                onClick={() => setOpen((v) => !v)}
              >
                <ChevronRight size={13} className="error-notice-chevron" aria-hidden="true" />
                Details
              </button>
            )}
          </div>
        )}

        {rows && (
          <div className="error-notice-details" id={panelId} hidden={!open}>
            <dl>
              {rows.map((r) => (
                <div className="error-notice-row" key={r.label}>
                  <dt>{r.label}</dt>
                  <dd>{r.value}</dd>
                </div>
              ))}
            </dl>
            <button
              type="button"
              className={`error-notice-copy${copied === 'ok' ? ' is-copied' : ''}`}
              onClick={() => void handleCopy()}
            >
              {copied === 'ok' ? <Check size={13} /> : <Copy size={13} />}
              <span aria-live="polite">
                {copied === 'ok' ? 'Copied' : copied === 'failed' ? 'Copy failed' : 'Copy'}
              </span>
            </button>
          </div>
        )}
      </div>
      {onDismiss && (
        <button type="button" className="error-notice-dismiss" aria-label="Dismiss" onClick={onDismiss}>
          <X size={14} />
        </button>
      )}
    </div>
  )
}
