import { useEffect, useRef, useState } from 'react'
import { Wallet, ChevronDown, Check, Pencil, X, PiggyBank } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useAccountSelection } from '../lib/accountSelection'
import type { AccountInfo } from '../lib/accountSelection'
import { useAccountLabels } from '../lib/accountLabels'
import { upsertNickname, setSavingsAccount } from '../lib/api'

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
  const { displayLabel, nicknameOf, isSavings } = useAccountLabels()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  // Which label's savings flag is currently being toggled (for a subtle pending look).
  const [savingBusy, setSavingBusy] = useState<string | null>(null)

  // Inline nickname editing — which raw label is being renamed, plus the draft.
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

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

  // Closing the popover cancels any in-flight edit.
  useEffect(() => {
    if (!open) setEditing(null)
  }, [open])

  useEffect(() => {
    if (editing !== null) inputRef.current?.focus()
  }, [editing])

  // Nothing to filter by yet — keep the control out of the way.
  if (accounts.length === 0) return null

  function beginEdit(label: string) {
    setEditing(label)
    setDraft(nicknameOf(label) ?? '')
  }

  async function saveEdit(label: string) {
    setSaving(true)
    try {
      // Blank draft reverts to the canonical label server-side.
      await upsertNickname(label, draft.trim())
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['accounts', 'nicknames'] }),
        queryClient.invalidateQueries({ queryKey: ['accounts', 'universe'] }),
      ])
      setEditing(null)
    } catch {
      // Leave the editor open so the user can retry; nothing crashes.
    } finally {
      setSaving(false)
    }
  }

  // Flip an account's savings designation, then refresh the shared caches so the
  // "Savings" tag and the savings-rate math both pick it up.
  async function toggleSavings(label: string, next: boolean) {
    setSavingBusy(label)
    try {
      await setSavingsAccount(label, next)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['accounts', 'nicknames'] }),
        queryClient.invalidateQueries({ queryKey: ['accounts', 'universe'] }),
      ])
    } catch {
      // Endpoint may not be live yet — leave state untouched; nothing crashes.
    } finally {
      setSavingBusy(null)
    }
  }

  const label = allSelected
    ? 'All accounts'
    : selected.length === 0
      ? 'No accounts'
      : selected.length === 1
        ? displayLabel(selected[0])
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
              const isEditing = editing === a.label
              const sav = isSavings(a.label)
              const savBusy = savingBusy === a.label

              if (isEditing) {
                return (
                  <div key={a.label} className="account-select-row editing">
                    <input
                      ref={inputRef}
                      className="account-select-edit-input"
                      value={draft}
                      placeholder={a.label}
                      disabled={saving}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') { e.preventDefault(); void saveEdit(a.label) }
                        if (e.key === 'Escape') { e.preventDefault(); setEditing(null) }
                      }}
                      aria-label={`Nickname for ${a.label}`}
                    />
                    <button
                      type="button"
                      className="account-select-icon-btn save"
                      title="Save nickname (blank reverts)"
                      disabled={saving}
                      onClick={() => void saveEdit(a.label)}
                    >
                      {saving ? <span className="spinner" style={{ width: 12, height: 12 }} /> : <Check size={13} strokeWidth={3} />}
                    </button>
                    <button
                      type="button"
                      className="account-select-icon-btn cancel"
                      title="Cancel"
                      disabled={saving}
                      onClick={() => setEditing(null)}
                    >
                      <X size={13} strokeWidth={3} />
                    </button>
                  </div>
                )
              }

              return (
                <div key={a.label} className={`account-select-row${on ? ' on' : ''}${sav ? ' savings' : ''}`}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={on}
                    className="account-select-hit"
                    onClick={() => toggle(a.label)}
                  >
                    <span className={`account-select-check${on ? ' on' : ''}`}>
                      {on && <Check size={11} strokeWidth={3} />}
                    </span>
                    <span className="account-select-name" title={a.label}>{displayLabel(a.label)}</span>
                    {sav && <span className="account-select-savings-tag">Savings</span>}
                    {bal && <span className="account-select-balance mono">{bal}</span>}
                  </button>
                  <button
                    type="button"
                    className={`account-select-icon-btn savings${sav ? ' on' : ''}`}
                    title={sav
                      ? 'Savings account — money moved in counts toward your savings rate. Click to unmark.'
                      : 'Mark as a savings account — money moved in counts toward your savings rate'}
                    aria-pressed={sav}
                    disabled={savBusy}
                    onClick={(e) => { e.stopPropagation(); void toggleSavings(a.label, !sav) }}
                  >
                    {savBusy
                      ? <span className="spinner" style={{ width: 12, height: 12 }} />
                      : <PiggyBank size={13} fill={sav ? 'currentColor' : 'none'} />}
                  </button>
                  <button
                    type="button"
                    className="account-select-icon-btn pencil"
                    title={`Rename ${a.label}`}
                    onClick={(e) => { e.stopPropagation(); beginEdit(a.label) }}
                  >
                    <Pencil size={12} />
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
