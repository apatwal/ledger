import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { X, Copy, AlertCircle } from 'lucide-react'
import { getDuplicates, dismissDuplicates, deleteTransaction } from '../lib/api'
import type { DuplicateGroup } from '../lib/types'
import { acctKey } from '../lib/queryKeys'

interface Props {
  // The dashboard/ledger filters this modal should respect.
  params?: { start_date?: string; end_date?: string; account?: string; accounts?: string[] }
  onClose: () => void
  // Fired after any mutation so the opener can refresh its duplicate count.
  onChanged?: () => void
}

function fmt(n: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n)
}

export default function DuplicatesModal({ params, onClose, onChanged }: Props) {
  const queryClient = useQueryClient()
  // Key includes the full filter window (dates + account selection) so cached
  // duplicate groups are never shown for the wrong filter.
  const dupQuery = useQuery({
    queryKey: ['duplicates', {
      start_date: params?.start_date,
      end_date: params?.end_date,
      account: params?.account,
      accounts: acctKey(params?.accounts),
    }],
    queryFn: () => getDuplicates(params),
  })
  const groups: DuplicateGroup[] = dupQuery.data ?? []
  const loading = dupQuery.isPending
  const [actionError, setActionError] = useState<string | null>(null)
  const error = actionError ?? (dupQuery.error instanceof Error ? dupQuery.error.message : dupQuery.error ? 'Failed to load duplicates' : null)

  // key of the group with a pending in-flight action, so we can disable its buttons
  const [busyKey, setBusyKey] = useState<string | null>(null)
  // key of the group whose "Delete extra" is awaiting confirmation
  const [confirmKey, setConfirmKey] = useState<string | null>(null)

  // Any duplicate mutation changes the ledger — refresh duplicates + the lists
  // and stats that derive from them (all filtered variants via prefix).
  const invalidateAfterDupChange = () => {
    void queryClient.invalidateQueries({ queryKey: ['duplicates'] })
    void queryClient.invalidateQueries({ queryKey: ['transactions'] })
    void queryClient.invalidateQueries({ queryKey: ['stats'] })
  }

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  async function handleDismiss(group: DuplicateGroup) {
    setBusyKey(group.group_key)
    setActionError(null)
    try {
      await dismissDuplicates(group.transactions.map((t) => t.id))
      onChanged?.()
      invalidateAfterDupChange()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Failed to dismiss group')
    } finally {
      setBusyKey(null)
      setConfirmKey(null)
    }
  }

  async function handleDeleteExtra(group: DuplicateGroup) {
    setBusyKey(group.group_key)
    setActionError(null)
    try {
      // Keep the oldest (smallest id); delete every other row in the group.
      const sorted = [...group.transactions].sort((a, b) => a.id - b.id)
      const toDelete = sorted.slice(1)
      for (const tx of toDelete) {
        await deleteTransaction(tx.id)
      }
      onChanged?.()
      invalidateAfterDupChange()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Failed to delete extra charges')
    } finally {
      setBusyKey(null)
      setConfirmKey(null)
    }
  }

  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wide">
        <div className="modal-header">
          <span className="modal-title">Possible duplicates</span>
          <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <div className="modal-body" style={{ maxHeight: '65vh', overflowY: 'auto' }}>
          {error && (
            <div className="dup-error">
              <AlertCircle size={15} />
              {error}
            </div>
          )}

          {loading ? (
            <div className="loading-state" style={{ padding: 32 }}>
              <div className="spinner spinner-lg" />
              Scanning for duplicates…
            </div>
          ) : groups.length === 0 ? (
            <div className="empty-state" style={{ padding: 40 }}>
              <div className="empty-state-icon">
                <Copy size={36} />
              </div>
              <div>No duplicates 🎉</div>
              <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>
                Every charge in this window looks unique.
              </div>
            </div>
          ) : (
            <>
              <p style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 16, lineHeight: 1.6 }}>
                These expenses share the same date, amount, merchant and account — likely double-charges
                or re-imported rows. Keep one and delete the extras, or dismiss the group if it's genuine.
              </p>
              <div className="dup-list">
                {groups.map((g) => {
                  const busy = busyKey === g.group_key
                  return (
                    <div key={g.group_key} className="dup-group">
                      <div className="dup-group-main">
                        <div className="dup-group-title">
                          <span className="dup-merchant">{g.description ?? 'Unlabelled charge'}</span>
                          <span className="badge badge-duplicate">charged {g.count}×</span>
                        </div>
                        <div className="dup-group-meta">
                          <span className="mono">{g.date}</span>
                          <span className="dup-dot">·</span>
                          <span className="mono">{fmt(g.amount)}</span>
                          <span className="dup-dot">·</span>
                          {g.account
                            ? <span className="badge badge-account">{g.account}</span>
                            : <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>Unassigned</span>}
                        </div>
                        <div className="dup-extra">
                          Over-charged by <strong>{fmt(g.total_extra)}</strong>
                        </div>
                      </div>
                      <div className="dup-group-actions">
                        {confirmKey === g.group_key ? (
                          <>
                            <span className="dup-confirm-label">
                              Delete {g.count - 1} extra{g.count - 1 === 1 ? '' : 's'}, keep 1?
                            </span>
                            <button
                              className="btn btn-danger btn-sm"
                              onClick={() => void handleDeleteExtra(g)}
                              disabled={busy}
                            >
                              {busy ? <span className="spinner" style={{ width: 12, height: 12 }} /> : 'Confirm delete'}
                            </button>
                            <button
                              className="btn btn-ghost btn-sm"
                              onClick={() => setConfirmKey(null)}
                              disabled={busy}
                            >
                              Cancel
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              className="btn btn-secondary btn-sm"
                              onClick={() => void handleDismiss(g)}
                              disabled={busy}
                            >
                              {busy ? <span className="spinner" style={{ width: 12, height: 12 }} /> : null}
                              Not a duplicate
                            </button>
                            <button
                              className="btn btn-danger btn-sm"
                              onClick={() => setConfirmKey(g.group_key)}
                              disabled={busy}
                            >
                              Delete extra
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </div>

        <div className="modal-footer">
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  )
}
