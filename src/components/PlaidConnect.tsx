import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { usePlaidLink } from 'react-plaid-link'
import type { PlaidLinkOnSuccessMetadata } from 'react-plaid-link'
import {
  Landmark,
  Link2,
  RefreshCw,
  Trash2,
  Wallet,
  Plus,
  CheckCircle,
  XCircle,
} from 'lucide-react'
import {
  getPlaidStatus,
  getNetWorth,
  createPlaidLinkToken,
  exchangePlaidPublicToken,
  plaidSync,
  deletePlaidItem,
} from '../lib/api'
import type { PlaidItem, PlaidAccount, PlaidSyncResult } from '../lib/types'
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

// Money for card balances & the net-worth figure.
function fmtMoney(amount: number | null | undefined, currency?: string | null, cents = false): string {
  if (amount === null || amount === undefined) return '—'
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency || 'USD',
      maximumFractionDigits: cents ? 2 : 0,
      minimumFractionDigits: cents ? 2 : 0,
    }).format(amount)
  } catch {
    return `$${cents ? amount.toFixed(2) : Math.round(amount)}`
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

// Nicely-cased account type/subtype for the little card chip.
function typeLabel(a: PlaidAccount): string {
  const raw = (a.subtype || a.type || 'account').replace(/_/g, ' ')
  return raw.replace(/\b\w/g, (c) => c.toUpperCase())
}
// app_account often bakes the mask in ("CREDIT CARD ••2875"); strip it since the
// card renders the masked number on its own line, card-style.
function cleanNick(a: PlaidAccount): string {
  const base = a.app_account || a.name || 'Account'
  if (!a.mask) return base
  const stripped = base.replace(new RegExp(`\\s*[•·]+\\s*${a.mask}\\s*$`), '').trim()
  return stripped || base
}
function balanceLabel(a: PlaidAccount): string {
  const t = (a.type || '').toLowerCase()
  if (t === 'investment' || t === 'brokerage') return 'Value'
  if (t === 'credit' || t === 'loan') return 'Balance'
  return 'Available'
}

// ── Card colour: turn a brand hex into a legible gradient + text colour. ─────
function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '')
  const s = h.length === 3 ? h.split('').map((c) => c + c).join('') : h
  const n = parseInt(s.slice(0, 6), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}
function relLuminance([r, g, b]: [number, number, number]): number {
  const a = [r, g, b].map((v) => {
    const s = v / 255
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4)
  })
  return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2]
}
function scaleRgb([r, g, b]: [number, number, number], f: number): string {
  const c = (v: number) => Math.max(0, Math.min(255, Math.round(v * f)))
  return `rgb(${c(r)}, ${c(g)}, ${c(b)})`
}
interface CardTheme { bg1: string; bg2: string; fg: string; fgSoft: string; line: string }
function cardTheme(hex?: string | null): CardTheme {
  const valid = hex && /^#?[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$/.test(hex.trim())
  const base = valid ? hex!.trim() : '#2c2e27' // slate ink fallback for brand-less banks
  const rgb = hexToRgb(base)
  const light = relLuminance(rgb) < 0.45 // dark card → light text
  return {
    bg1: scaleRgb(rgb, 1),
    bg2: scaleRgb(rgb, light ? 0.62 : 1.14),
    fg: light ? '#f7f8f2' : '#1b1c18',
    fgSoft: light ? 'rgba(247,248,242,0.74)' : 'rgba(27,28,24,0.62)',
    line: light ? 'rgba(247,248,242,0.24)' : 'rgba(27,28,24,0.16)',
  }
}

const prefersReducedMotion =
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches

// Gentle count-up for the net-worth figure; static when motion is unwelcome.
function useCountUp(target: number, ready: boolean): number {
  const [val, setVal] = useState(prefersReducedMotion ? target : 0)
  useEffect(() => {
    if (!ready || prefersReducedMotion) { setVal(target); return }
    let raf = 0
    const start = performance.now()
    const dur = 750
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / dur)
      const eased = 1 - Math.pow(1 - t, 3)
      setVal(target * eased)
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ready])
  return val
}

// Per-card tilt for the horizontal fan: symmetric about centre, with the total
// spread capped so a large wallet doesn't over-rotate the edge cards.
function fanRotation(idx: number, count: number): number {
  if (count <= 1) return 0
  const step = Math.min(4.5, 22 / (count - 1))
  return (idx - (count - 1) / 2) * step
}

export default function PlaidConnect() {
  const queryClient = useQueryClient()
  const statusQuery = useQuery({ queryKey: ['plaid', 'status'], queryFn: getPlaidStatus })
  const netWorthQuery = useQuery({ queryKey: ['stats', 'net-worth'], queryFn: getNetWorth })
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

  const items = status?.items ?? []

  // One card per account (a bank can hold several). Keep a stable flat order so
  // the fan geometry doesn't reshuffle on re-render.
  const cards = useMemo(
    () => items.flatMap((item) => item.accounts.map((account) => ({ item, account }))),
    [items],
  )

  // ─── States ─────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="wallet-shell">
        <div className="wallet-shell-head">
          <Wallet size={18} style={{ color: 'var(--green-deep)' }} />
          <span className="t">Your wallet</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--ink-faint)', fontSize: 13 }}>
          <span className="spinner" style={{ width: 15, height: 15 }} />
          Loading your accounts…
        </div>
      </div>
    )
  }

  // Not configured — same guidance as before, in the wallet shell.
  if (!status?.configured) {
    return (
      <div className="wallet-shell">
        <div className="wallet-shell-head">
          <Landmark size={18} style={{ color: 'var(--ink-faint)' }} />
          <span className="t" style={{ color: 'var(--ink-soft)' }}>Bank sync is off</span>
          <span className="wallet-env" style={{ marginLeft: 'auto' }}>Not configured</span>
        </div>
        <p style={{ fontSize: 13, color: 'var(--ink-faint)', lineHeight: 1.7, margin: 0 }}>
          Automatic bank sync (via Plaid) lets you link an account and pull transactions in without CSV
          exports. It's not enabled on this server. To turn it on, set{' '}
          <code className="plaid-code">PLAID_CLIENT_ID</code> and{' '}
          <code className="plaid-code">PLAID_SECRET</code> in the API environment and restart.
        </p>
      </div>
    )
  }

  const busy = preparing || finishing
  const nw = netWorthQuery.data ?? null
  const nwReady = !netWorthQuery.isPending
  const hasNetWorth = !!nw && (nw.net_worth !== 0 || nw.assets !== 0 || nw.liabilities !== 0)

  return (
    <div className="wallet-page">
      {/* ── Net-worth hero band + controls ── */}
      <section className="wallet-hero" aria-label="Net worth and bank controls">
        <div className="wallet-hero-nw">
          <NetWorthPanel
            netWorth={nw}
            ready={nwReady}
            has={hasNetWorth}
            bankCount={items.length}
            accountCount={cards.length}
          />
        </div>

        <div className="wallet-hero-controls">
          <div className="wallet-actions-row">
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
            {status.env && <span className="wallet-env">{status.env}</span>}
          </div>
          <p className="wallet-lede">
            Securely link a bank through Plaid — transactions sync in on their own, no CSV needed.
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
        </div>
      </section>

      {/* ── The wallet: cards fanned into a billfold pocket ── */}
      {cards.length === 0 ? (
        <div className="wallet-empty">
          <div className="wallet-empty-ico"><Plus size={22} /></div>
          <div className="wallet-empty-title">Your wallet is empty</div>
          <div className="wallet-empty-sub">
            Link your first bank to tuck a card in here — balances and transactions sync on their own.
          </div>
          <button
            className="btn btn-primary btn-sm"
            onClick={() => void handleConnect()}
            disabled={busy}
            style={{ marginTop: 2 }}
          >
            <Link2 size={14} /> Connect a bank
          </button>
        </div>
      ) : (
        <div className="wallet">
          <div className="wallet-track" role="list" aria-label="Linked accounts">
            {cards.map(({ item, account }, idx) => (
              <WalletCard
                key={account.account_id}
                item={item}
                account={account}
                rot={fanRotation(idx, cards.length)}
                z={idx + 1}
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
          <div className="wallet-pocket" aria-hidden />
        </div>
      )}
    </div>
  )
}

// ─── Net-worth hero ─────────────────────────────────────────────────────────
// Reuses the app's /stats/net-worth figure (same source as the Dashboard tile)
// so the numbers stay consistent: assets − liabilities across linked balances.

function NetWorthPanel({
  netWorth, ready, has, bankCount, accountCount,
}: {
  netWorth: import('../lib/types').NetWorth | null
  ready: boolean
  has: boolean
  bankCount: number
  accountCount: number
}) {
  const target = has && netWorth ? netWorth.net_worth : 0
  const animated = useCountUp(target, ready && has)
  const cur = netWorth?.currency || 'USD'

  return (
    <>
      <div className="wallet-nw-eyebrow">
        <Wallet size={13} />
        <span>Net worth</span>
      </div>

      {!ready ? (
        <>
          <div className="wallet-nw-shimmer fig" />
          <div className="wallet-nw-shimmer sub" />
        </>
      ) : has && netWorth ? (
        <>
          <div className="wallet-nw-figure">{fmtMoney(animated, cur, true)}</div>
          <dl className="wallet-nw-ledger">
            <div className="wallet-nw-cell">
              <dt>Assets</dt>
              <dd className="pos">{fmtMoney(netWorth.assets, cur)}</dd>
            </div>
            <div className="wallet-nw-cell">
              <dt>Credit &amp; loans</dt>
              <dd className="neg">{fmtMoney(netWorth.liabilities, cur)}</dd>
            </div>
          </dl>
          <div className="wallet-nw-linked">
            {bankCount} bank{bankCount === 1 ? '' : 's'} · {accountCount} account{accountCount === 1 ? '' : 's'} linked
          </div>
        </>
      ) : (
        <>
          <div className="wallet-nw-figure muted">—</div>
          <div className="wallet-nw-linked">Connect a bank to see your net worth.</div>
        </>
      )}
    </>
  )
}

// ─── A single account card ────────────────────────────────────────────────

function WalletCard({
  item, account, rot, z, syncing, removing, confirming, disabled,
  onSync, onAskDisconnect, onCancelDisconnect, onConfirmDisconnect,
}: {
  item: PlaidItem
  account: PlaidAccount
  rot: number
  z: number
  syncing: boolean
  removing: boolean
  confirming: boolean
  disabled: boolean
  onSync: () => void
  onAskDisconnect: () => void
  onCancelDisconnect: () => void
  onConfirmDisconnect: () => void
}) {
  const theme = cardTheme(item.institution_color)
  const inst = item.institution_name ?? 'Bank'
  const nick = cleanNick(account)
  const bal = account.current ?? account.available
  const degraded = item.status && item.status.toLowerCase() !== 'active' && item.status.toLowerCase() !== 'good'

  const style = {
    '--card-bg1': theme.bg1,
    '--card-bg2': theme.bg2,
    '--card-fg': theme.fg,
    '--card-fg-soft': theme.fgSoft,
    '--card-line': theme.line,
    '--rot': `${rot}deg`,
    zIndex: z,
  } as React.CSSProperties

  return (
    <article
      className="wallet-card"
      style={style}
      role="listitem"
      tabIndex={0}
      aria-label={`${inst} — ${nick}, ${balanceLabel(account).toLowerCase()} ${fmtMoney(bal, account.currency, true)}`}
    >
      <div className="wallet-card-inner">
      <div className="wallet-card-top">
        {item.institution_logo ? (
          <img className="wallet-card-logo" src={`data:image/png;base64,${item.institution_logo}`} alt="" />
        ) : (
          <span className="wallet-card-monogram" aria-hidden>{inst.charAt(0)}</span>
        )}
        <span className="wallet-card-inst">{inst}</span>
        {degraded && <span className="wallet-card-degraded" title={`Item status: ${item.status}`}>{item.status}</span>}
        <span className="wallet-card-type">{typeLabel(account)}</span>
      </div>

      <div className="wallet-card-nick">{nick}</div>
      {account.mask && <div className="wallet-card-mask">•••• •••• {account.mask}</div>}

      {/* Foot — balance + actions. Tucked into the pocket at rest; the lift reveals it. */}
      <div className="wallet-card-foot">
        <div className="wallet-card-bal">
          <span className="wallet-card-bal-fig">{fmtMoney(bal, account.currency, true)}</span>
          <span className="wallet-card-bal-label">{balanceLabel(account)}</span>
        </div>
        <div className="wallet-card-fold">
          <span className="wallet-card-synced">{formatSynced(item.last_synced_at)}</span>
          <div className="wallet-card-fold-actions">
          {confirming ? (
            <>
              <button className="wallet-cbtn danger" onClick={onConfirmDisconnect} disabled={removing}>
                {removing ? <span className="spinner" style={{ width: 12, height: 12 }} /> : 'Confirm'}
              </button>
              <button className="wallet-cbtn ghost" onClick={onCancelDisconnect} disabled={removing}>
                Cancel
              </button>
            </>
          ) : (
            <>
              <button
                className="wallet-cbtn"
                onClick={onSync}
                disabled={disabled || syncing}
                title={`Pull the latest transactions from ${inst}`}
              >
                {syncing ? <span className="spinner" style={{ width: 12, height: 12 }} /> : <RefreshCw size={12} />}
                Sync
              </button>
              <button
                className="wallet-cbtn ghost"
                onClick={onAskDisconnect}
                disabled={disabled}
                title={`Disconnect ${inst}`}
              >
                <Trash2 size={12} />
              </button>
            </>
          )}
          </div>
        </div>
      </div>
      </div>
    </article>
  )
}
