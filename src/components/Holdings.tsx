import { useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import { TrendingUp, TrendingDown, LineChart, Wallet, PieChart as PieIcon, Layers } from 'lucide-react'
import { getHoldings, getSnapshots, captureSnapshot, getInvestmentFlows } from '../lib/api'
import type { Holding } from '../lib/types'
import { useAccountLabels } from '../lib/accountLabels'

// ── Ledger palette — same inks/greens/ochres the Dashboard charts use ─────────
const C = {
  ink: '#1b1c18',
  inkFaint: '#84877a',
  green: '#1f6b4a',
  red: '#a8322e',
  gold: '#9a6b16',
  rule: '#d6d8cb',
  paper: '#fbfbf6',
}
const PIE_COLORS = [
  '#1f6b4a', '#a8322e', '#9a6b16', '#3d6b8c',
  '#6b4a7a', '#5a7a3a', '#b5793a', '#4c4e44',
  '#7a9a6b', '#8c5a5a',
]

// ── Formatters ────────────────────────────────────────────────────────────────
function fmtMoney(n: number | null | undefined, currency?: string | null): string {
  if (n === null || n === undefined) return '—'
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency || 'USD',
      maximumFractionDigits: 2,
    }).format(n)
  } catch {
    return `$${n.toFixed(2)}`
  }
}

function fmtMoney0(n: number, currency?: string | null): string {
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: currency || 'USD', maximumFractionDigits: 0 }).format(n)
  } catch {
    return `$${Math.round(n)}`
  }
}

function fmtSigned(n: number, currency?: string | null): string {
  const sign = n > 0 ? '+' : n < 0 ? '−' : ''
  return `${sign}${fmtMoney(Math.abs(n), currency)}`
}

function fmtPctSigned(n: number): string {
  const sign = n > 0 ? '+' : n < 0 ? '−' : ''
  return `${sign}${Math.abs(n).toFixed(2)}%`
}

function fmtQty(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 }).format(n)
}

// Plaid security_type values → presentable labels.
function niceAssetClass(s: string): string {
  const k = s.trim().toLowerCase()
  const map: Record<string, string> = {
    etf: 'ETF',
    'mutual fund': 'Mutual fund',
    mutualfund: 'Mutual fund',
    equity: 'Equity',
    cash: 'Cash',
    'fixed income': 'Fixed income',
    fixedincome: 'Fixed income',
    bond: 'Bond',
    derivative: 'Derivative',
    'money market': 'Money market',
    cryptocurrency: 'Crypto',
    crypto: 'Crypto',
    loan: 'Loan',
    other: 'Other',
  }
  if (map[k]) return map[k]
  return k ? k.replace(/\b\w/g, (c) => c.toUpperCase()) : 'Other'
}

// The unrealized gain for a holding: prefer the server value, else derive it.
function gainOf(h: Holding): number | null {
  if (h.unrealized_gain !== null && h.unrealized_gain !== undefined) return h.unrealized_gain
  if (h.cost_basis !== null && h.cost_basis !== undefined && h.value !== null && h.value !== undefined) {
    return h.value - h.cost_basis
  }
  return null
}

function hasBasis(h: Holding): boolean {
  return h.cost_basis !== null && h.cost_basis !== undefined
}

// Collapse Plaid's redundant "X - Y" names (where the halves are equal or nested)
// down to the distinct/shorter half, and squash repeated whitespace.
function cleanName(raw: string): string {
  const s = raw.replace(/\s+/g, ' ').trim()
  const parts = s.split(/\s+-\s+/)
  if (parts.length === 2) {
    const a = parts[0].trim()
    const b = parts[1].trim()
    const la = a.toLowerCase()
    const lb = b.toLowerCase()
    if (la === lb) return a
    if (la.includes(lb)) return b
    if (lb.includes(la)) return a
  }
  return s
}

function truncate(s: string, n = 22): string {
  return s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s
}

// Concise chip label: ticker when present, else a cleaned + truncated name.
function shortSecurityLabel(h: Pick<Holding, 'ticker_symbol' | 'security_name'>): string {
  const t = (h.ticker_symbol ?? '').trim()
  if (t) return t.toUpperCase()
  const name = (h.security_name ?? '').trim()
  return name ? truncate(cleanName(name)) : 'Unknown'
}

// Full (but de-duplicated) name for tooltips.
function fullSecurityName(h: Pick<Holding, 'ticker_symbol' | 'security_name'>): string {
  const name = (h.security_name ?? '').trim()
  if (name) return cleanName(name)
  const t = (h.ticker_symbol ?? '').trim()
  return t ? t.toUpperCase() : 'Unknown'
}

// Grouping key so the same security held in multiple accounts collapses to one:
// ticker (uppercased) when present, else the cleaned name.
function securityKey(h: Holding): string {
  const t = (h.ticker_symbol ?? '').trim().toUpperCase()
  if (t) return t
  return cleanName(h.security_name ?? '').toLowerCase() || 'unknown'
}

interface Security {
  key: string
  short: string
  full: string
  value: number
  quantity: number
  cost_basis: number | null
}

// Sum value / quantity / cost_basis across every lot of each security.
function aggregateSecurities(holdings: Holding[]): Security[] {
  const map = new Map<string, Security>()
  for (const h of holdings) {
    const key = securityKey(h)
    const prev = map.get(key) ?? { key, short: shortSecurityLabel(h), full: fullSecurityName(h), value: 0, quantity: 0, cost_basis: null }
    prev.value += h.value ?? 0
    prev.quantity += h.quantity ?? 0
    if (hasBasis(h)) prev.cost_basis = (prev.cost_basis ?? 0) + (h.cost_basis ?? 0)
    map.set(key, prev)
  }
  return [...map.values()]
}

function securityGain(s: Security): number | null {
  return s.cost_basis !== null ? s.value - s.cost_basis : null
}

// ── Shared chart chrome ─────────────────────────────────────────────────────────
const TOOLTIP_STYLE = {
  backgroundColor: C.paper,
  border: `1px solid ${C.ink}`,
  borderRadius: '4px',
  color: C.ink,
  fontFamily: "'IBM Plex Mono', monospace",
  fontSize: '12px',
  boxShadow: '0 12px 32px -12px rgba(27,28,24,0.4)',
  padding: '10px 14px',
}

function MoneyTooltip({ active, payload, label }: {
  active?: boolean
  payload?: Array<{ name?: string; value?: number; color?: string }>
  label?: string
}) {
  if (!active || !payload?.length) return null
  return (
    <div style={TOOLTIP_STYLE}>
      {label !== undefined && (
        <div style={{ marginBottom: 6, fontWeight: 600, color: C.inkFaint, fontSize: 11, letterSpacing: '0.06em', textTransform: 'uppercase' }}>{label}</div>
      )}
      {payload.map((p, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
          <span style={{ width: 8, height: 8, background: p.color, display: 'inline-block' }} />
          <span style={{ color: C.inkFaint, fontSize: 12 }}>{p.name}</span>
          <span style={{ fontWeight: 600, marginLeft: 'auto' }}>{fmtMoney(p.value)}</span>
        </div>
      ))}
    </div>
  )
}

function PieTooltip({ active, payload }: {
  active?: boolean
  payload?: Array<{ name?: string; value?: number; payload?: { pct?: number; full?: string } }>
}) {
  if (!active || !payload?.length) return null
  const d = payload[0]
  return (
    <div style={TOOLTIP_STYLE}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{d.payload?.full ?? d.name}</div>
      <div style={{ color: C.ink, fontSize: 13 }}>{fmtMoney(d.value)}</div>
      {d.payload?.pct !== undefined && (
        <div style={{ color: C.inkFaint, fontSize: 12 }}>{d.payload.pct.toFixed(1)}%</div>
      )}
    </div>
  )
}

// Tooltip for the signed gain/loss bars (green/red per sign). Shows the full name.
function GainTooltip({ active, payload }: {
  active?: boolean
  payload?: Array<{ payload?: { name?: string; full?: string; gain?: number } }>
}) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload
  if (!d) return null
  const g = d.gain ?? 0
  return (
    <div style={TOOLTIP_STYLE}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{d.full ?? d.name}</div>
      <div style={{ color: g >= 0 ? C.green : C.red, fontSize: 13, fontWeight: 600 }}>{fmtSigned(g)}</div>
    </div>
  )
}

// Compact donut legend — a wrapping row of "swatch · short label · pct" chips,
// so long full names never blow the legend across the whole card.
function PctLegend({ payload }: {
  payload?: Array<{ value?: string; color?: string; payload?: { pct?: number } }>
}) {
  if (!payload?.length) return null
  return (
    <ul className="viz-legend">
      {payload.map((entry, i) => (
        <li key={i} className="viz-legend-item" title={entry.value}>
          <span className="viz-legend-swatch" style={{ background: entry.color }} />
          <span className="viz-legend-label">{entry.value}</span>
          {entry.payload?.pct !== undefined && (
            <span className="viz-legend-pct">{Math.round(entry.payload.pct)}%</span>
          )}
        </li>
      ))}
    </ul>
  )
}

const AXIS_TICK = { fill: C.inkFaint, fontSize: 11, fontFamily: 'IBM Plex Mono' }

function shortDate(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export default function Holdings() {
  const queryClient = useQueryClient()
  const { displayLabel } = useAccountLabels()

  const holdingsQ = useQuery({ queryKey: ['plaid', 'holdings'], queryFn: getHoldings })
  const holdings = useMemo(() => holdingsQ.data ?? [], [holdingsQ.data])
  const loading = holdingsQ.isPending
  // A 503 means Plaid isn't configured — a "feature off" state, distinct from a
  // real error or "no holdings yet".
  const errMsg = holdingsQ.error instanceof Error ? holdingsQ.error.message : holdingsQ.error ? 'Could not load holdings' : null
  const disabled = errMsg?.startsWith('503') ?? false
  const error = disabled ? null : errMsg

  // Secondary data — only fetched once holdings resolve so we don't hit these
  // endpoints when Plaid is off. Their errors are swallowed into empty states.
  const snapshotsQ = useQuery({
    queryKey: ['investments', 'snapshots'],
    queryFn: () => getSnapshots(),
    enabled: holdingsQ.isSuccess,
  })
  const flowsQ = useQuery({
    queryKey: ['stats', 'investment-flows', 'month'],
    queryFn: () => getInvestmentFlows('month'),
    enabled: holdingsQ.isSuccess,
  })

  const capture = useMutation({
    mutationFn: captureSnapshot,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['investments', 'snapshots'] }),
  })

  const currency = holdings.find((h) => h.currency)?.currency ?? 'USD'

  // ── Derived data ──────────────────────────────────────────────────────────
  const derived = useMemo(() => {
    const total = holdings.reduce((s, h) => s + (h.value ?? 0), 0)
    const accountSet = new Set(holdings.map((h) => h.account ?? 'Unassigned'))

    const basisHoldings = holdings.filter(hasBasis)
    const totalCost = basisHoldings.reduce((s, h) => s + (h.cost_basis ?? 0), 0)
    const totalGain = basisHoldings.reduce((s, h) => s + (gainOf(h) ?? 0), 0)
    const totalGainPct = totalCost > 0 ? (totalGain / totalCost) * 100 : null

    // Allocation by account (raw label kept for tooltip fidelity; display name for legend).
    const byAcctMap = new Map<string, number>()
    for (const h of holdings) {
      const acct = h.account ?? 'Unassigned'
      byAcctMap.set(acct, (byAcctMap.get(acct) ?? 0) + (h.value ?? 0))
    }
    const byAccount = [...byAcctMap.entries()]
      .map(([acct, value]) => ({ name: acct === 'Unassigned' ? 'Unassigned' : displayLabel(acct), value, pct: total > 0 ? (value / total) * 100 : 0 }))
      .sort((a, b) => b.value - a.value)

    // Aggregate lots into one row per security (same holding across accounts collapses).
    const securities = aggregateSecurities(holdings)

    // Allocation by holding — top 8 securities by value, remainder folded into "Other".
    const sorted = [...securities].sort((a, b) => b.value - a.value)
    const TOP = 8
    const top = sorted.slice(0, TOP).map((s) => ({ name: s.short, full: s.full, value: s.value }))
    const restVal = sorted.slice(TOP).reduce((s, r) => s + r.value, 0)
    const byHolding = (restVal > 0 ? [...top, { name: 'Other', full: 'Other securities', value: restVal }] : top)
      .map((r) => ({ ...r, pct: total > 0 ? (r.value / total) * 100 : 0 }))

    // Asset-class mix by security_type.
    const byClassMap = new Map<string, number>()
    for (const h of holdings) {
      if (h.security_type === null || h.security_type === undefined || !String(h.security_type).trim()) continue
      const t = niceAssetClass(String(h.security_type))
      byClassMap.set(t, (byClassMap.get(t) ?? 0) + (h.value ?? 0))
    }
    const byClass = [...byClassMap.entries()].map(([type, value]) => ({ type, value })).sort((a, b) => b.value - a.value)

    // Per-security gain/loss (only securities with a cost basis): the top 12 movers
    // by absolute gain/loss, then sorted by signed value for display.
    const gainRows = securities
      .filter((s) => s.cost_basis !== null)
      .map((s) => ({ name: s.short, full: s.full, gain: securityGain(s) ?? 0 }))
      .sort((a, b) => Math.abs(b.gain) - Math.abs(a.gain))
      .slice(0, 12)
      .sort((a, b) => b.gain - a.gain)

    return { total, accounts: accountSet.size, positions: securities.length, totalGain, totalGainPct, byAccount, byHolding, byClass, gainRows, hasBasisData: basisHoldings.length > 0 }
  }, [holdings, displayLabel])

  // Portfolio value over time — aggregate per-account snapshots to a portfolio total.
  const valueSeries = useMemo(() => {
    const snaps = snapshotsQ.data ?? []
    const byTime = new Map<string, number>()
    for (const s of snaps) byTime.set(s.captured_at, (byTime.get(s.captured_at) ?? 0) + (s.value ?? 0))
    return [...byTime.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([captured_at, value]) => ({ captured_at, label: shortDate(captured_at), value }))
  }, [snapshotsQ.data])

  const flows = flowsQ.data ?? []

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Portfolio</div>
        <h1 className="page-title">Investments</h1>
        <p className="page-subtitle">How your linked investment accounts are allocated and performing.</p>
      </div>

      {loading ? (
        <div className="loading-state">
          <div className="spinner spinner-lg" />
          Loading holdings…
        </div>
      ) : disabled ? (
        <div className="card holdings-disabled">
          <div className="plaid-head">
            <LineChart size={18} style={{ color: 'var(--text-muted)' }} />
            <div style={{ fontWeight: 600, fontSize: 15, color: 'var(--text-secondary)' }}>
              Investment sync is off
            </div>
            <span className="badge badge-transfer" style={{ marginLeft: 'auto' }}>Not configured</span>
          </div>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.7, margin: 0 }}>
            Connect a bank with an investment account (via Plaid) to see your holdings here.
            Bank sync isn't enabled on this server.
          </p>
        </div>
      ) : error ? (
        <div className="error-state" style={{ padding: 32 }}>
          <TrendingUp size={28} />
          <div>{error}</div>
        </div>
      ) : holdings.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">
            <LineChart size={40} />
          </div>
          <div>No investment holdings found on your linked accounts.</div>
        </div>
      ) : (
        <>
          {/* 1 ── Summary tiles */}
          <div className="kpi-grid">
            <div className="kpi-card">
              <div className="kpi-header">
                <span className="kpi-label">Portfolio value</span>
                <span className="kpi-icon accent"><Wallet size={16} /></span>
              </div>
              <div className="kpi-value default mono">{fmtMoney(derived.total, currency)}</div>
              <div className="kpi-sub">across {derived.accounts} account{derived.accounts === 1 ? '' : 's'}</div>
            </div>

            <div className="kpi-card">
              <div className="kpi-header">
                <span className="kpi-label">Unrealized gain/loss</span>
                <span className={`kpi-icon ${derived.hasBasisData && derived.totalGain < 0 ? 'expense' : 'income'}`}>
                  {derived.totalGain < 0 ? <TrendingDown size={16} /> : <TrendingUp size={16} />}
                </span>
              </div>
              {derived.hasBasisData ? (
                <>
                  <div className={`kpi-value mono ${derived.totalGain >= 0 ? 'income' : 'expense'}`}>{fmtSigned(derived.totalGain, currency)}</div>
                  <div className="kpi-sub">{derived.totalGainPct !== null ? fmtPctSigned(derived.totalGainPct) : '—'} on cost basis</div>
                </>
              ) : (
                <>
                  <div className="kpi-value default mono">—</div>
                  <div className="kpi-sub">no cost-basis data</div>
                </>
              )}
            </div>

            <div className="kpi-card">
              <div className="kpi-header">
                <span className="kpi-label">Positions</span>
                <span className="kpi-icon accent"><Layers size={16} /></span>
              </div>
              <div className="kpi-value default mono">{derived.positions}</div>
              <div className="kpi-sub">distinct securities · {holdings.length} lot{holdings.length === 1 ? '' : 's'}</div>
            </div>

            <div className="kpi-card">
              <div className="kpi-header">
                <span className="kpi-label">Accounts</span>
                <span className="kpi-icon accent"><PieIcon size={16} /></span>
              </div>
              <div className="kpi-value default mono">{derived.accounts}</div>
              <div className="kpi-sub">holding investments</div>
            </div>
          </div>

          {/* 2 ── Allocation donuts */}
          <div className="holdings-charts-grid">
            <div className="chart-card">
              <div className="chart-title-row">
                <div className="chart-title">Allocation by account</div>
                <span className="chart-note">{derived.byAccount.length} account{derived.byAccount.length === 1 ? '' : 's'}</span>
              </div>
              <ResponsiveContainer width="100%" height={260}>
                <PieChart>
                  <Pie data={derived.byAccount} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={88} innerRadius={52} paddingAngle={3} isAnimationActive={false}>
                    {derived.byAccount.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                  </Pie>
                  <Tooltip content={<PieTooltip />} />
                  <Legend content={<PctLegend />} />
                </PieChart>
              </ResponsiveContainer>
            </div>

            <div className="chart-card">
              <div className="chart-title-row">
                <div className="chart-title">Allocation by holding</div>
                <span className="chart-note">top {Math.min(8, derived.byHolding.length)}{derived.byHolding.some((h) => h.name === 'Other') ? ' + other' : ''}</span>
              </div>
              <ResponsiveContainer width="100%" height={260}>
                <PieChart>
                  <Pie data={derived.byHolding} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={88} innerRadius={52} paddingAngle={3} isAnimationActive={false}>
                    {derived.byHolding.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                  </Pie>
                  <Tooltip content={<PieTooltip />} />
                  <Legend content={<PctLegend />} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* 3 ── Asset-class mix (horizontal bars) */}
          {derived.byClass.length > 0 && (
            <div className="chart-card" style={{ marginBottom: 16 }}>
              <div className="chart-title-row">
                <div className="chart-title">Asset-class mix</div>
                <span className="chart-note">by security type</span>
              </div>
              <ResponsiveContainer width="100%" height={Math.max(160, derived.byClass.length * 44)}>
                <BarChart data={derived.byClass} layout="vertical" margin={{ top: 4, right: 24, left: 8, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="2 4" stroke={C.rule} horizontal={false} />
                  <XAxis type="number" tick={AXIS_TICK} axisLine={false} tickLine={false} tickFormatter={(v: number) => fmtMoney0(v, currency)} />
                  <YAxis type="category" dataKey="type" tick={AXIS_TICK} axisLine={{ stroke: C.ink }} tickLine={false} width={110} />
                  <Tooltip content={<MoneyTooltip />} cursor={{ fill: C.green, fillOpacity: 0.06 }} />
                  <Bar dataKey="value" name="Value" fill={C.green} radius={[0, 2, 2, 0]} isAnimationActive={false} barSize={20} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* 4 ── Unrealized gain/loss per position */}
          {derived.hasBasisData && derived.gainRows.length > 0 && (
            <div className="chart-card" style={{ marginBottom: 16 }}>
              <div className="chart-title-row">
                <div className="chart-title">Unrealized gain / loss</div>
                <span className="chart-note">
                  top {derived.gainRows.length} mover{derived.gainRows.length === 1 ? '' : 's'} ·{' '}
                  <span style={{ color: derived.totalGain >= 0 ? C.green : C.red, fontWeight: 600 }}>
                    Total {fmtSigned(derived.totalGain, currency)}{derived.totalGainPct !== null ? ` (${fmtPctSigned(derived.totalGainPct)})` : ''}
                  </span>
                </span>
              </div>
              <ResponsiveContainer width="100%" height={Math.min(430, Math.max(190, derived.gainRows.length * 32))}>
                <BarChart data={derived.gainRows} layout="vertical" margin={{ top: 4, right: 24, left: 8, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="2 4" stroke={C.rule} horizontal={false} />
                  <XAxis type="number" tick={AXIS_TICK} axisLine={false} tickLine={false} tickFormatter={(v: number) => fmtMoney0(v, currency)} />
                  <YAxis type="category" dataKey="name" tick={AXIS_TICK} axisLine={{ stroke: C.ink }} tickLine={false} width={112} interval={0} />
                  <Tooltip content={<GainTooltip />} cursor={{ fill: C.ink, fillOpacity: 0.04 }} />
                  <Bar dataKey="gain" name="Gain/Loss" radius={[0, 2, 2, 0]} isAnimationActive={false} barSize={18}>
                    {derived.gainRows.map((r, i) => <Cell key={i} fill={r.gain >= 0 ? C.green : C.red} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* 5 ── Contributions vs dividends over time */}
          <div className="chart-card" style={{ marginBottom: 16 }}>
            <div className="chart-title-row">
              <div className="chart-title">Contributions vs dividends</div>
              <span className="chart-note">by month</span>
            </div>
            {flows.length === 0 ? (
              <div className="empty-state" style={{ padding: 32 }}>
                {flowsQ.isPending && flowsQ.fetchStatus !== 'idle'
                  ? 'Loading flows…'
                  : 'No contribution or dividend activity recorded yet.'}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <BarChart data={flows} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="2 4" stroke={C.rule} vertical={false} />
                  <XAxis dataKey="period" tick={AXIS_TICK} axisLine={{ stroke: C.ink }} tickLine={false} />
                  <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} tickFormatter={(v: number) => fmtMoney0(v, currency)} />
                  <Tooltip content={<MoneyTooltip />} cursor={{ fill: C.green, fillOpacity: 0.06 }} />
                  <Legend wrapperStyle={{ fontSize: 12, color: C.inkFaint, fontFamily: 'IBM Plex Mono' }} />
                  <Bar dataKey="contributions" name="Contributions" fill={C.green} radius={[2, 2, 0, 0]} isAnimationActive={false} />
                  <Bar dataKey="dividends" name="Dividends" fill={C.gold} radius={[2, 2, 0, 0]} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>

          {/* 6 ── Portfolio value over time */}
          <div className="chart-card" style={{ marginBottom: 16 }}>
            <div className="chart-title-row">
              <div className="chart-title">Portfolio value over time</div>
              <span className="chart-note">on each sync</span>
            </div>
            {valueSeries.length < 2 ? (
              <div className="empty-state" style={{ padding: 32, flexDirection: 'column', gap: 14 }}>
                <div style={{ maxWidth: 460, textAlign: 'center', lineHeight: 1.6 }}>
                  We're recording your portfolio value on each sync — the trend line will appear here soon.
                </div>
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={() => capture.mutate()}
                  disabled={capture.isPending}
                >
                  {capture.isPending ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <LineChart size={14} />}
                  {capture.isPending ? 'Capturing…' : 'Capture now'}
                </button>
                {capture.isError && (
                  <div style={{ fontSize: 12, color: C.red }}>Couldn't capture a snapshot right now.</div>
                )}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={280}>
                <AreaChart data={valueSeries} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                  <defs>
                    <linearGradient id="pvGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={C.green} stopOpacity={0.22} />
                      <stop offset="95%" stopColor={C.green} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="2 4" stroke={C.rule} vertical={false} />
                  <XAxis dataKey="label" tick={AXIS_TICK} axisLine={{ stroke: C.ink }} tickLine={false} />
                  <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} tickFormatter={(v: number) => fmtMoney0(v, currency)} />
                  <Tooltip content={<MoneyTooltip />} cursor={{ stroke: C.inkFaint, strokeDasharray: '3 3' }} />
                  <Area type="monotone" dataKey="value" name="Portfolio value" stroke={C.green} fill="url(#pvGrad)" strokeWidth={2} isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>

          {/* 7 ── Holdings table (with cost basis + gain/loss) */}
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Security</th>
                  <th>Ticker</th>
                  <th>Account</th>
                  <th style={{ textAlign: 'right' }}>Quantity</th>
                  <th style={{ textAlign: 'right' }}>Price</th>
                  <th style={{ textAlign: 'right' }}>Value</th>
                  <th style={{ textAlign: 'right' }}>Cost basis</th>
                  <th style={{ textAlign: 'right' }}>Gain/Loss</th>
                </tr>
              </thead>
              <tbody>
                {holdings.map((h, i) => {
                  const g = gainOf(h)
                  const gpct = h.unrealized_gain_pct ?? (hasBasis(h) && h.cost_basis ? ((g ?? 0) / h.cost_basis) * 100 : null)
                  return (
                    <tr key={`${h.ticker_symbol ?? h.security_name ?? 'row'}-${i}`}>
                      <td style={{ fontWeight: 600 }}>
                        {h.security_name ?? <span style={{ color: 'var(--text-muted)' }}>—</span>}
                        {h.institution && (
                          <div style={{ fontSize: 12, color: 'var(--text-muted)', fontWeight: 400 }}>{h.institution}</div>
                        )}
                      </td>
                      <td>
                        {h.ticker_symbol
                          ? <span className="badge badge-account">{h.ticker_symbol}</span>
                          : <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>—</span>}
                      </td>
                      <td style={{ color: 'var(--text-secondary)', fontSize: 13 }} title={h.account ?? undefined}>
                        {h.account ? displayLabel(h.account) : '—'}
                      </td>
                      <td className="num">{fmtQty(h.quantity)}</td>
                      <td className="num">{fmtMoney(h.price, h.currency)}</td>
                      <td className="num" style={{ fontWeight: 600 }}>{fmtMoney(h.value, h.currency)}</td>
                      <td className="num">{fmtMoney(h.cost_basis, h.currency)}</td>
                      <td className="num" style={{ fontWeight: 600, color: g === null ? 'var(--text-muted)' : g >= 0 ? 'var(--green)' : 'var(--red)' }}>
                        {g === null ? '—' : (
                          <>
                            {fmtSigned(g, h.currency)}
                            {gpct !== null && gpct !== undefined && (
                              <span style={{ display: 'block', fontSize: 11, fontWeight: 400 }}>{fmtPctSigned(gpct)}</span>
                            )}
                          </>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
