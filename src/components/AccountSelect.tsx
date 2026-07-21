import { useEffect, useRef, useState } from 'react'
import { Wallet, ChevronDown, Check } from 'lucide-react'
import { useAccountSelection } from '../lib/accountSelection'
import type { AccountInfo } from '../lib/accountSelection'

function fmtBalance(a: AccountInfo): string | null {
  const amount = a.current ?? a.available
  if (amount === null || amount === undefined) return null
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: a.currency || 'USD',
      maximumFractionDigits: 0,
    }).format(amount)
  } catch {
    return `$${Math.round(amount)}`
  }
}

// Global account multi-select — drives every data view in the app.
export default function AccountSelect() {
  const { accounts, selected, isSelected, toggle, selectAll, clear, allSelected } = useAccountSelection()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Nothing to filter by yet — keep the control out of the way.
  if (accounts.length === 0) return null

  const label = allSelected
    ? 'All accounts'
    : selected.length === 0
      ? 'No accounts'
      : selected.length === 1
        ? selected[0]
        : `${selected.length} of ${accounts.length} accounts`

  return (
    <div className="account-select" ref={rootRef}>
      <button
        type="button"
        className={`account-select-trigger${open ? ' open' : ''}`}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Choose which accounts drive the whole app"
      >
        <Wallet size={14} />
        <span className="account-select-label">{label}</span>
        <ChevronDown size={14} className="account-select-caret" />
      </button>

      {open && (
        <div className="account-select-popover" role="listbox" aria-multiselectable="true">
          <div className="account-select-actions">
            <button type="button" className="account-select-action" onClick={selectAll}>Select all</button>
            <span className="account-select-dot">·</span>
            <button type="button" className="account-select-action" onClick={clear}>Clear</button>
          </div>
          <div className="account-select-list">
            {accounts.map((a) => {
              const on = isSelected(a.label)
              const bal = fmtBalance(a)
              return (
                <button
                  key={a.label}
                  type="button"
                  role="option"
                  aria-selected={on}
                  className={`account-select-row${on ? ' on' : ''}`}
                  onClick={() => toggle(a.label)}
                >
                  <span className={`account-select-check${on ? ' on' : ''}`}>
                    {on && <Check size={11} strokeWidth={3} />}
                  </span>
                  <span className="account-select-name">{a.label}</span>
                  {bal && <span className="account-select-balance mono">{bal}</span>}
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
