import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { usePlaidLink } from 'react-plaid-link'
import type { PlaidLinkOnSuccessMetadata } from 'react-plaid-link'
import {
  Landmark,
  Link2,
  RefreshCw,
  Trash2,
  CreditCard,
  CheckCircle,
  XCircle,
  Info,
  Building2,
} from 'lucide-react'
import {
  getPlaidStatus,
  createPlaidLinkToken,
  exchangePlaidPublicToken,
  plaidSync,
  deletePlaidItem,
} from '../lib/api'
import type { PlaidItem, PlaidSyncResult } from '../lib/types'
import { invalidateLedger } from '../lib/queryKeys'

// Human-friendly "last synced" — relative for recent, absolute for older.
function formatSynced(iso: string | null): string {
  if (!iso) return 'Never synced'
  const then = new Date(iso)
  const ms = Date.now() - then.getTime()
  if (Number.isNaN(ms)) return 'Never synced'
  const min = Math.floor(ms / 60000)
  if (min < 1) return 'Synced just now'
  if (min < 60) return `Synced ${min} min ago`
  const hrs = Math.floor(min / 60)
  if (hrs < 24) return `Synced ${hrs} hr${hrs === 1 ? '' : 's'} ago`
  const days = Math.floor(hrs / 24)
  if (days < 7) return `Synced ${days} day${days === 1 ? '' : 's'} ago`
  return `Synced ${then.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}`
}

// Compact account balance for the connected-accounts chips.
function fmtBal(amount: number | null | undefined, currency?: string | null): string | null {
  if (amount === null || amount === undefined) return null
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency || 'USD',
      maximumFractionDigits: 0,
    }).format(amount)
  } catch {
    return `$${Math.round(amount)}`
  }
}

function syncSummary(r: PlaidSyncResult): string {
  const parts = [
    `${r.added} added`,
    `${r.modified} updated`,
    `${r.removed} removed`,
  ]
  const acrossItems = r.items_synced === 1 ? '' : ` across ${r.items_synced} banks`
  return `Sync complete${acrossItems}: ${parts.join(', ')}.`
}

export default function PlaidConnect() {
  const queryClient = useQueryClient()
  const statusQuery = useQuery({ queryKey: ['plaid', 'status'], queryFn: getPlaidStatus })
  const status = statusQuery.data ?? null
  const loading = statusQuery.isPending
  const [actionError, setActionError] = useState<string | null>(null)
  const error = actionError
    ?? (statusQuery.error instanceof Error ? statusQuery.error.message : statusQuery.error ? 'Could not load bank sync status' : null)

  // Link flow
  const [linkToken, setLinkToken] = useState<string | null>(null)
  const [preparing, setPreparing] = useState(false) // generating link token
  const [finishing, setFinishing] = useState(false) // exchange + first sync
  const [pendingOpen, setPendingOpen] = useState(false) // want to auto-open Link once the fresh token's handler is ready
  // The open() handler that was live when we requested a new token. usePlaidLink
  // keeps the old handler (and `ready`) around until the new token's handler is
  // built, so we only auto-open once `open` has actually changed away from this.
  const openAtRequest = useRef<Function | null>(null)

  // Per-action state
  const [syncingId, setSyncingId] = useState<number | 'all' | null>(null)
  const [confirmId, setConfirmId] = useState<number | null>(null)
  const [removingId, setRemovingId] = useState<number | null>(null)
  const [msg, setMsg] = useState<string | null>(null)

  // Bank sync mutates the ledger (transactions, accounts, holdings) and the plaid
  // status itself — refresh everything via prefix invalidation.
  const refresh = useCallback(() => { invalidateLedger(queryClient) }, [queryClient])

  // ─── Link success → exchange → first sync ──────────────────────────────────
  const onLinkSuccess = useCallback(
    async (publicToken: string, _metadata: PlaidLinkOnSuccessMetadata) => {
      setLinkToken(null)
      setPendingOpen(false)
      setFinishing(true)
      setMsg(null)
      setActionError(null)
      try {
        const item = await exchangePlaidPublicToken(publicToken)
        const res = await plaidSync(item.id)
        setMsg(`Connected ${item.institution_name ?? 'your bank'}. ${syncSummary(res)}`)
        refresh()
      } catch (e) {
        setActionError(e instanceof Error ? e.message : 'Could not finish connecting your bank')
      } finally {
        setFinishing(false)
      }
    },
    [refresh],
  )

  const { open, ready } = usePlaidLink({
    token: linkToken,
    onSuccess: (public_token, metadata) => void onLinkSuccess(public_token, metadata),
    onExit: () => {
      // User bailed out of Link; drop the one-shot token and cancel the pending open.
      setLinkToken(null)
      setPendingOpen(false)
    },
  })

  // Auto-open Link once the token's handler is ready. We must wait for `open` to
  // be rebuilt for the CURRENT token: usePlaidLink leaves `ready` true and keeps
  // the previous token's (now-destroyed) `open` between connects, so firing on
  // `ready` alone would call a stale handler and never open the new one. Gating
  // on `open !== openAtRequest.current` ties the auto-open to the correct
  // handler transition, so every click reliably opens Link with its fresh token.
  useEffect(() => {
    if (pendingOpen && ready && linkToken && open !== openAtRequest.current) {
      setPendingOpen(false)
      open()
    }
  }, [pendingOpen, ready, linkToken, open])

  async function handleConnect() {
    setPreparing(true)
    setActionError(null)
    setMsg(null)
    try {
      const { link_token } = await createPlaidLinkToken()
      openAtRequest.current = open // remember the current (soon-to-be-stale) handler
      setPendingOpen(true)
      setLinkToken(link_token)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Could not start bank connection')
    } finally {
      setPreparing(false)
    }
  }

  async function handleSync(itemId?: number) {
    setSyncingId(itemId ?? 'all')
    setActionError(null)
    setMsg(null)
    try {
      const res = await plaidSync(itemId)
      setMsg(syncSummary(res))
      refresh()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Sync failed')
    } finally {
      setSyncingId(null)
    }
  }

  async function handleDisconnect(id: number) {
    setRemovingId(id)
    setActionError(null)
    setMsg(null)
    try {
      await deletePlaidItem(id)
      setConfirmId(null)
      setMsg('Bank disconnected. Its imported transactions were removed.')
      refresh()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Could not disconnect')
    } finally {
      setRemovingId(null)
    }
  }

  // ─── States ─────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="card plaid-card">
        <div className="plaid-head">
          <Landmark size={18} style={{ color: 'var(--accent-hover)' }} />
          <div style={{ fontWeight: 600, fontSize: 15 }}>Connect a bank</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text-muted)', fontSize: 13 }}>
          <span className="spinner" style={{ width: 15, height: 15 }} />
          Checking bank sync…
        </div>
      </div>
    )
  }

  // Not configured — mirror the Assistant "feature disabled" treatment.
  if (!status?.configured) {
    return (
      <div className="card plaid-card plaid-disabled">
        <div className="plaid-head">
          <Landmark size={18} style={{ color: 'var(--text-muted)' }} />
          <div style={{ fontWeight: 600, fontSize: 15, color: 'var(--text-secondary)' }}>
            Bank sync is off
          </div>
          <span className="badge badge-transfer" style={{ marginLeft: 'auto' }}>Not configured</span>
        </div>
        <p style={{ fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.7, margin: 0 }}>
          Automatic bank sync (via Plaid) lets you link an account and pull transactions in without CSV
          exports. It's not enabled on this server. To turn it on, set{' '}
          <code className="plaid-code">PLAID_CLIENT_ID</code> and{' '}
          <code className="plaid-code">PLAID_SECRET</code> in the API environment and restart.
          You can still import a CSV below.
        </p>
      </div>
    )
  }

  const items = status.items ?? []
  const busy = preparing || finishing

  return (
    <div className="card plaid-card">
      <div className="plaid-head">
        <Landmark size={18} style={{ color: 'var(--accent-hover)' }} />
        <div style={{ fontWeight: 600, fontSize: 15 }}>Connect a bank</div>
        {status.env && (
          <span className="badge badge-statement badge-statement-bank" style={{ marginLeft: 8 }}>
            {status.env}
          </span>
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {items.length > 0 && (
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => void handleSync()}
              disabled={syncingId !== null || busy}
              title="Pull the latest transactions from every connected bank"
            >
              {syncingId === 'all'
                ? <span className="spinner" style={{ width: 13, height: 13 }} />
                : <RefreshCw size={14} />}
              Sync all
            </button>
          )}
          <button
            className="btn btn-primary btn-sm"
            onClick={() => void handleConnect()}
            disabled={busy || syncingId !== null}
          >
            {preparing
              ? <><span className="spinner" style={{ width: 13, height: 13 }} /> Opening…</>
              : finishing
                ? <><span className="spinner" style={{ width: 13, height: 13 }} /> Finishing…</>
                : <><Link2 size={14} /> Connect a bank</>}
          </button>
        </div>
      </div>

      <p style={{ fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.6, margin: '0 0 4px' }}>
        Securely link a bank through Plaid and pull transactions in automatically — no CSV needed.
      </p>

      {msg && (
        <div className="plaid-note success">
          <CheckCircle size={15} />
          <span>{msg}</span>
        </div>
      )}
      {error && (
        <div className="plaid-note danger">
          <XCircle size={15} />
          <span>{error}</span>
        </div>
      )}

      {items.length === 0 ? (
        <div className="plaid-empty">
          <Info size={15} style={{ flexShrink: 0 }} />
          <span>No banks connected yet. Click <strong>Connect a bank</strong> to link one.</span>
        </div>
      ) : (
        <div className="plaid-items">
          {items.map((item) => (
            <PlaidItemRow
              key={item.id}
              item={item}
              syncing={syncingId === item.id}
              removing={removingId === item.id}
              confirming={confirmId === item.id}
              disabled={syncingId !== null || busy}
              onSync={() => void handleSync(item.id)}
              onAskDisconnect={() => setConfirmId(item.id)}
              onCancelDisconnect={() => setConfirmId(null)}
              onConfirmDisconnect={() => void handleDisconnect(item.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Connected institution row ──────────────────────────────────────────────

function PlaidItemRow({
  item, syncing, removing, confirming, disabled,
  onSync, onAskDisconnect, onCancelDisconnect, onConfirmDisconnect,
}: {
  item: PlaidItem
  syncing: boolean
  removing: boolean
  confirming: boolean
  disabled: boolean
  onSync: () => void
  onAskDisconnect: () => void
  onCancelDisconnect: () => void
  onConfirmDisconnect: () => void
}) {
  const degraded = item.status && item.status.toLowerCase() !== 'active' && item.status.toLowerCase() !== 'good'
  const accent = item.institution_color || undefined

  return (
    <div
      className="plaid-item"
      style={accent ? { borderLeft: `3px solid ${accent}` } : undefined}
    >
      <div className="plaid-item-main">
        <div className="plaid-item-title">
          {item.institution_logo ? (
            <img
              className="plaid-item-logo"
              src={`data:image/png;base64,${item.institution_logo}`}
              alt=""
            />
          ) : (
            <Building2 size={15} style={{ color: accent ?? 'var(--accent-hover)', flexShrink: 0 }} />
          )}
          <span className="plaid-item-name">{item.institution_name ?? 'Bank'}</span>
          {degraded && (
            <span className="badge badge-needs-review" title={`Item status: ${item.status}`}>
              {item.status}
            </span>
          )}
        </div>

        {item.accounts.length > 0 && (
          <div className="plaid-accounts">
            {item.accounts.map((a) => {
              const bal = fmtBal(a.current ?? a.available, a.currency)
              return (
                <span key={a.account_id} className="plaid-account">
                  <CreditCard size={12} style={{ flexShrink: 0 }} />
                  <span className="plaid-account-name">{a.name ?? a.app_account ?? 'Account'}</span>
                  {a.mask && <span className="mono plaid-mask">••{a.mask}</span>}
                  {a.subtype && <span className="plaid-account-sub">{a.subtype}</span>}
                  {bal && <span className="mono plaid-account-bal">{bal}</span>}
                </span>
              )
            })}
          </div>
        )}

        <div className="plaid-item-meta mono">{formatSynced(item.last_synced_at)}</div>
      </div>

      <div className="plaid-item-actions">
        <button
          className="btn btn-secondary btn-sm"
          onClick={onSync}
          disabled={disabled || syncing || confirming}
          title="Pull the latest transactions from this bank"
        >
          {syncing
            ? <span className="spinner" style={{ width: 12, height: 12 }} />
            : <RefreshCw size={13} />}
          Sync now
        </button>

        {confirming ? (
          <>
            <button className="btn btn-danger btn-sm" onClick={onConfirmDisconnect} disabled={removing}>
              {removing ? <span className="spinner" style={{ width: 12, height: 12 }} /> : 'Confirm'}
            </button>
            <button className="btn btn-ghost btn-sm" onClick={onCancelDisconnect} disabled={removing}>
              Cancel
            </button>
          </>
        ) : (
          <button
            className="btn btn-ghost btn-icon btn-sm"
            onClick={onAskDisconnect}
            disabled={disabled}
            title="Disconnect this bank"
            style={{ color: 'var(--danger)' }}
          >
            <Trash2 size={14} />
          </button>
        )}
      </div>
    </div>
  )
}
