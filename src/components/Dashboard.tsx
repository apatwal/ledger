import { useEffect, useState, useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
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
import { TrendingDown, Sparkles, Copy } from 'lucide-react'
import {
  getStatsSummary,
  getStatsByCategory,
  getStatsOverTime,
  getStatsByAccount,
  getInsights,
  getAssistantStatus,
  getDuplicates,
} from '../lib/api'
import type { Granularity } from '../lib/types'
import DatePicker from './DatePicker'
import DuplicatesModal from './DuplicatesModal'
import { useAccountSelection } from '../lib/accountSelection'
import { acctKey } from '../lib/queryKeys'
import { iconForCategory } from '../lib/categoryIcons'

// Ledger palette — inks, greens, ochres pulled from the design system.
const PIE_COLORS = [
  '#1f6b4a', '#a8322e', '#9a6b16', '#3d6b8c',
  '#6b4a7a', '#5a7a3a', '#b5793a', '#4c4e44',
  '#7a9a6b', '#8c5a5a',
]

const C = {
  ink: '#1b1c18',
  inkFaint: '#84877a',
  green: '#1f6b4a',
  red: '#a8322e',
  gold: '#9a6b16',
  rule: '#d6d8cb',
  paper: '#fbfbf6',
}

function fmt(n: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n)
}

function fmtSigned(n: number): string {
  const sign = n > 0 ? '+' : n < 0 ? '−' : ''
  return `${sign}${fmt(Math.abs(n))}`
}

function fmtPct(n: number): string {
  return `${n.toFixed(1)}%`
}

const CUSTOM_TOOLTIP_STYLE = {
  backgroundColor: C.paper,
  border: `1px solid ${C.ink}`,
  borderRadius: '4px',
  color: C.ink,
  fontFamily: "'IBM Plex Mono', monospace",
  fontSize: '12px',
  boxShadow: '0 12px 32px -12px rgba(27,28,24,0.4)',
}

function CustomTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ name: string; value: number; color: string }>; label?: string }) {
  if (!active || !payload?.length) return null
  return (
    <div style={{ ...CUSTOM_TOOLTIP_STYLE, padding: '10px 14px' }}>
      <div style={{ marginBottom: 6, fontWeight: 600, color: C.inkFaint, fontSize: 11, letterSpacing: '0.06em', textTransform: 'uppercase' }}>{label}</div>
      {payload.map((p) => (
        <div key={p.name} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
          <span style={{ width: 8, height: 8, background: p.color, display: 'inline-block' }} />
          <span style={{ color: C.inkFaint, fontSize: 12 }}>{p.name}</span>
          <span style={{ fontWeight: 600, marginLeft: 'auto' }}>{fmt(p.value)}</span>
        </div>
      ))}
    </div>
  )
}

function PieTooltip({ active, payload }: { active?: boolean; payload?: Array<{ name: string; value: number; payload: { pct: number } }> }) {
  if (!active || !payload?.length) return null
  const d = payload[0]
  return (
    <div style={{ ...CUSTOM_TOOLTIP_STYLE, padding: '10px 14px' }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{d.name}</div>
      <div style={{ color: C.ink, fontSize: 13 }}>{fmt(d.value)}</div>
      <div style={{ color: C.inkFaint, fontSize: 12 }}>{fmtPct(d.payload.pct)}</div>
    </div>
  )
}

function isoDay(d: Date): string {
  return d.toISOString().split('T')[0]
}

function daysAgo(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return isoDay(d)
}

type PresetKey = '30d' | '90d' | 'ytd' | '1y'

const PRESETS: { key: PresetKey; label: string }[] = [
  { key: '30d', label: '30 days' },
  { key: '90d', label: '90 days' },
  { key: 'ytd', label: 'YTD' },
  { key: '1y', label: '1 year' },
]

function presetRange(key: PresetKey): { start: string; end: string; granularity: Granularity } {
  const today = isoDay(new Date())
  switch (key) {
    case '30d':
      return { start: daysAgo(30), end: today, granularity: 'day' }
    case '90d':
      return { start: daysAgo(90), end: today, granularity: 'week' }
    case 'ytd':
      return { start: `${today.slice(0, 4)}-01-01`, end: today, granularity: 'month' }
    case '1y':
      return { start: daysAgo(365), end: today, granularity: 'month' }
  }
}

export default function Dashboard() {
  const initial = presetRange('30d')

  const [startDate, setStartDate] = useState(initial.start)
  const [endDate, setEndDate] = useState(initial.end)
  const [granularity, setGranularity] = useState<Granularity>(initial.granularity)
  const [activePreset, setActivePreset] = useState<PresetKey | null>('30d')

  const queryClient = useQueryClient()

  // Which accounts are in view is driven globally by the header selector.
  const { accountsParam, isSelected, toggle, balanceOf } = useAccountSelection()
  // The account selection is stringified into every stats/duplicates key so
  // cached data is never shown for the wrong filter.
  const accounts = accountsParam()
  const acctPart = acctKey(accounts)

  // Headline stats. by-account is the per-card breakdown — always across all
  // cards (date range only), so its key omits the account selection.
  const summaryQ = useQuery({
    queryKey: ['stats', 'summary', { start_date: startDate, end_date: endDate, accounts: acctPart }],
    queryFn: () => getStatsSummary({ start_date: startDate, end_date: endDate, accounts }),
  })
  const byCategoryQ = useQuery({
    queryKey: ['stats', 'by-category', { start_date: startDate, end_date: endDate, accounts: acctPart }],
    queryFn: () => getStatsByCategory({ start_date: startDate, end_date: endDate, accounts }),
  })
  const overTimeQ = useQuery({
    queryKey: ['stats', 'over-time', { start_date: startDate, end_date: endDate, accounts: acctPart, granularity }],
    queryFn: () => getStatsOverTime(granularity, { start_date: startDate, end_date: endDate, accounts }),
  })
  const byAccountQ = useQuery({
    queryKey: ['stats', 'by-account', { start_date: startDate, end_date: endDate }],
    queryFn: () => getStatsByAccount({ start_date: startDate, end_date: endDate }),
  })

  const summary = summaryQ.data ?? null
  const byCategory = byCategoryQ.data ?? []
  const overTime = overTimeQ.data ?? []
  const byAccount = byAccountQ.data ?? []
  const loading = summaryQ.isPending || byCategoryQ.isPending || overTimeQ.isPending || byAccountQ.isPending
  const firstError = summaryQ.error ?? byCategoryQ.error ?? overTimeQ.error ?? byAccountQ.error
  const error = firstError instanceof Error ? firstError.message : firstError ? 'Failed to load data' : null

  // Refresh/retry forces every filtered stats + duplicates variant to refetch.
  const loadData = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['stats'] })
    void queryClient.invalidateQueries({ queryKey: ['duplicates'] })
  }, [queryClient])

  // Duplicate detection (v7) — respects the current date/account filters.
  // Non-critical: on error we just show nothing (data ?? []).
  const duplicatesQ = useQuery({
    queryKey: ['duplicates', { start_date: startDate, end_date: endDate, accounts: acctPart }],
    queryFn: () => getDuplicates({ start_date: startDate, end_date: endDate, accounts }),
  })
  const duplicates = duplicatesQ.data ?? []
  const [dupModalOpen, setDupModalOpen] = useState(false)

  const assistantStatusQ = useQuery({ queryKey: ['assistant', 'status'], queryFn: getAssistantStatus })
  const aiEnabled = assistantStatusQ.data?.enabled ?? false

  const [insights, setInsights] = useState<string | null>(null)
  const [insightsLoading, setInsightsLoading] = useState(false)
  const [insightsError, setInsightsError] = useState<string | null>(null)

  const loadInsights = useCallback(async () => {
    setInsightsLoading(true)
    setInsightsError(null)
    try {
      const res = await getInsights({ start_date: startDate, end_date: endDate })
      setInsights(res.insights)
    } catch (e) {
      setInsightsError(e instanceof Error ? e.message : 'Failed to generate insights')
    } finally {
      setInsightsLoading(false)
    }
  }, [startDate, endDate])

  // Insights are tied to the selected range — clear them when the range changes.
  useEffect(() => {
    setInsights(null)
    setInsightsError(null)
  }, [startDate, endDate])

  // Duplicate params track the same window/accounts as the headline stats.
  const dupParams = {
    start_date: startDate,
    end_date: endDate,
    accounts,
  }

  const dupTotalExtra = duplicates.reduce((sum, g) => sum + g.total_extra, 0)

  const GRANULARITIES: Granularity[] = ['day', 'week', 'month', 'year']

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Statement · {startDate} – {endDate}</div>
        <h1 className="page-title">Dashboard</h1>
        <p className="page-subtitle">Where your money stands for the period.</p>
      </div>

      {/* Filters */}
      <div className="filters-bar">
        <div className="preset-group" role="group" aria-label="Quick date ranges">
          {PRESETS.map((p) => (
            <button
              key={p.key}
              className={`preset-btn${activePreset === p.key ? ' active' : ''}`}
              onClick={() => {
                const r = presetRange(p.key)
                setStartDate(r.start)
                setEndDate(r.end)
                setGranularity(r.granularity)
                setActivePreset(p.key)
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
        <div className="filter-group">
          <label className="filter-label" htmlFor="start">From</label>
          <DatePicker
            id="start"
            ariaLabel="From date"
            value={startDate}
            max={endDate}
            onChange={(iso) => { setStartDate(iso); setActivePreset(null) }}
          />
        </div>
        <div className="filter-group">
          <label className="filter-label" htmlFor="end">To</label>
          <DatePicker
            id="end"
            ariaLabel="To date"
            value={endDate}
            min={startDate}
            onChange={(iso) => { setEndDate(iso); setActivePreset(null) }}
          />
        </div>
        <div className="toggle-group">
          {GRANULARITIES.map((g) => (
            <button
              key={g}
              className={`toggle-btn${granularity === g ? ' active' : ''}`}
              onClick={() => setGranularity(g)}
            >
              {g.charAt(0).toUpperCase() + g.slice(1)}
            </button>
          ))}
        </div>
        <button className="btn btn-primary btn-sm" onClick={() => void loadData()}>
          Refresh
        </button>
        {aiEnabled && (
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => void loadInsights()}
            disabled={insightsLoading || loading}
            style={{ marginLeft: 'auto' }}
          >
            {insightsLoading ? (
              <span className="spinner" style={{ width: 13, height: 13 }} />
            ) : (
              <Sparkles size={14} />
            )}
            {insightsLoading ? 'Reading…' : 'Insights'}
          </button>
        )}
      </div>

      {/* Possible duplicates — attention card, only when there's at least one group */}
      {duplicates.length > 0 && (
        <div className="dup-alert">
          <div>
            <div className="dup-alert-head">
              <Copy size={13} />
              <span>Possible duplicates</span>
            </div>
            <div className="dup-alert-text">
              <strong>{duplicates.length}</strong> group{duplicates.length === 1 ? '' : 's'} of repeated charges
              {dupTotalExtra > 0 && (
                <> — up to <strong>{fmt(dupTotalExtra)}</strong> over-charged</>
              )}.
            </div>
          </div>
          <button className="btn btn-secondary btn-sm" onClick={() => setDupModalOpen(true)}>
            Review
          </button>
        </div>
      )}

      {loading && (
        <div className="loading-state">
          <div className="spinner spinner-lg" />
          Loading data…
        </div>
      )}

      {error && (
        <div className="error-state">
          <TrendingDown size={32} />
          <div>{error}</div>
          <button className="btn btn-secondary btn-sm" onClick={() => void loadData()}>
            Retry
          </button>
        </div>
      )}

      {!loading && !error && summary && (
        <>
          {/* The bottom line — in the black, or in the red */}
          <section className="balance">
            <div className="balance-eyebrow">
              <span>Net balance</span>
              <span className={`balance-status ${summary.net >= 0 ? 'pos' : 'neg'}`}>
                {summary.net >= 0 ? 'In the black' : 'In the red'}
              </span>
            </div>
            <div className={`balance-figure ${summary.net >= 0 ? 'pos' : 'neg'}`}>
              {fmtSigned(summary.net)}
            </div>

            <div className="rule-double" />

            <div className="balance-substats">
              <div className="substat">
                <span className="substat-label">Income</span>
                <span className="substat-val pos">{fmtSigned(summary.total_income)}</span>
                <div className="substat-sub">{summary.count} entries posted</div>
              </div>
              <div className="substat">
                <span className="substat-label">Expenses</span>
                <span className="substat-val neg">{fmtSigned(-summary.total_expense)}</span>
                <div className="substat-sub">Total outgoing</div>
              </div>
              <div className="substat">
                <span className="substat-label">Savings rate</span>
                <span className="substat-val gold">{fmtPct(summary.savings_rate * 100)}</span>
                <div className="substat-sub">Kept {fmt(summary.savings)}</div>
              </div>
            </div>
          </section>

          {/* Spending by card — the per-account breakdown */}
          {byAccount.length > 0 && (
            <section className="bycard">
              <div className="bycard-head">
                <div className="chart-title">Spending by card</div>
                <span className="chart-note">{byAccount.length} account{byAccount.length === 1 ? '' : 's'} · click to toggle</span>
              </div>
              <div className="bycard-list">
                {(() => {
                  const maxExpense = Math.max(...byAccount.map((a) => a.expense), 1)
                  return byAccount.map((a) => {
                    // Clicking a card toggles its membership in the global selection.
                    const on = isSelected(a.account)
                    const pct = Math.round((a.expense / maxExpense) * 100)
                    const bal = balanceOf(a.account)
                    const balAmt = bal?.current ?? bal?.available
                    return (
                      <button
                        key={a.account}
                        type="button"
                        className={`bycard-row${on ? ' active' : ' off'}`}
                        onClick={() => toggle(a.account)}
                        aria-pressed={on}
                        title={on ? `Hide ${a.account} everywhere` : `Show ${a.account} again`}
                      >
                        <div className="bycard-row-top">
                          <span className="bycard-name">{a.account}</span>
                          <span className="bycard-expense">{fmt(a.expense)}</span>
                        </div>
                        <div className="bycard-bar-track">
                          <div className="bycard-bar-fill" style={{ width: `${pct}%` }} />
                        </div>
                        <div className="bycard-meta">
                          <span>Income {fmt(a.income)}</span>
                          <span className={a.net >= 0 ? 'pos' : 'neg'}>Net {fmtSigned(a.net)}</span>
                          {balAmt !== null && balAmt !== undefined
                            ? <span>Balance {fmt(balAmt)}</span>
                            : <span>{a.count} {a.count === 1 ? 'entry' : 'entries'}</span>}
                        </div>
                      </button>
                    )
                  })
                })()}
              </div>
            </section>
          )}

          {/* AI insights */}
          {aiEnabled && (insights || insightsLoading || insightsError) && (
            <section className="insights-card">
              <div className="insights-head">
                <Sparkles size={14} />
                <span>Insights</span>
              </div>
              {insightsLoading && <div className="insights-loading">Reading your ledger…</div>}
              {insightsError && <div className="insights-error">{insightsError}</div>}
              {insights && !insightsLoading && <p className="insights-text">{insights}</p>}
            </section>
          )}

          {/* Charts Row 1: over-time + pie */}
          <div className="charts-grid">
            <div className="chart-card">
              <div className="chart-title-row">
                <div className="chart-title">Income vs Expenses</div>
                <span className="chart-note">by {granularity}</span>
              </div>
              {overTime.length === 0 ? (
                <div className="empty-state" style={{ padding: 32 }}>No entries in this range</div>
              ) : (
                <ResponsiveContainer width="100%" height={280}>
                  <AreaChart data={overTime} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                    <defs>
                      <linearGradient id="incomeGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor={C.green} stopOpacity={0.22} />
                        <stop offset="95%" stopColor={C.green} stopOpacity={0} />
                      </linearGradient>
                      <linearGradient id="expenseGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor={C.red} stopOpacity={0.2} />
                        <stop offset="95%" stopColor={C.red} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="2 4" stroke={C.rule} vertical={false} />
                    <XAxis
                      dataKey="period"
                      tick={{ fill: C.inkFaint, fontSize: 11, fontFamily: 'IBM Plex Mono' }}
                      axisLine={{ stroke: C.ink }}
                      tickLine={false}
                    />
                    <YAxis
                      tick={{ fill: C.inkFaint, fontSize: 11, fontFamily: 'IBM Plex Mono' }}
                      axisLine={false}
                      tickLine={false}
                      tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
                    />
                    <Tooltip content={<CustomTooltip />} cursor={{ stroke: C.inkFaint, strokeDasharray: '3 3' }} />
                    <Legend wrapperStyle={{ fontSize: 12, color: C.inkFaint, fontFamily: 'IBM Plex Mono' }} />
                    <Area
                      type="monotone"
                      dataKey="income"
                      name="Income"
                      stroke={C.green}
                      fill="url(#incomeGrad)"
                      strokeWidth={2}
                      isAnimationActive={false}
                    />
                    <Area
                      type="monotone"
                      dataKey="expense"
                      name="Expense"
                      stroke={C.red}
                      fill="url(#expenseGrad)"
                      strokeWidth={2}
                      isAnimationActive={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>

            <div className="chart-card">
              <div className="chart-title-row">
                <div className="chart-title">Where it went</div>
                <span className="chart-note">by category</span>
              </div>
              {byCategory.length === 0 ? (
                <div className="empty-state" style={{ padding: 32 }}>No data</div>
              ) : (
                <ResponsiveContainer width="100%" height={280}>
                  <PieChart>
                    <Pie
                      data={byCategory}
                      dataKey="total"
                      nameKey="category"
                      cx="50%"
                      cy="50%"
                      outerRadius={90}
                      innerRadius={50}
                      paddingAngle={3}
                      isAnimationActive={false}
                    >
                      {byCategory.map((_, i) => (
                        <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip content={<PieTooltip />} />
                    <Legend
                      formatter={(value) => {
                        const Icon = iconForCategory(String(value))
                        return (
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, color: C.inkFaint, verticalAlign: 'middle' }}>
                            <Icon size={12} /> {value}
                          </span>
                        )
                      }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* Charts Row 2: bar chart */}
          <div className="chart-card">
            <div className="chart-title-row">
              <div className="chart-title">The running balance</div>
              <span className="chart-note">income · expense · net</span>
            </div>
            {overTime.length === 0 ? (
              <div className="empty-state" style={{ padding: 32 }}>No entries in this range</div>
            ) : (
              <ResponsiveContainer width="100%" height={210}>
                <BarChart data={overTime} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="2 4" stroke={C.rule} vertical={false} />
                  <XAxis
                    dataKey="period"
                    tick={{ fill: C.inkFaint, fontSize: 11, fontFamily: 'IBM Plex Mono' }}
                    axisLine={{ stroke: C.ink }}
                    tickLine={false}
                  />
                  <YAxis
                    tick={{ fill: C.inkFaint, fontSize: 11, fontFamily: 'IBM Plex Mono' }}
                    axisLine={false}
                    tickLine={false}
                    tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
                  />
                  <Tooltip content={<CustomTooltip />} cursor={{ fill: C.green, fillOpacity: 0.06 }} />
                  <Legend wrapperStyle={{ fontSize: 12, color: C.inkFaint, fontFamily: 'IBM Plex Mono' }} />
                  <Bar dataKey="income" name="Income" fill={C.green} radius={[2, 2, 0, 0]} isAnimationActive={false} />
                  <Bar dataKey="expense" name="Expense" fill={C.red} radius={[2, 2, 0, 0]} isAnimationActive={false} />
                  <Bar dataKey="net" name="Net" fill={C.ink} radius={[2, 2, 0, 0]} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </>
      )}

      {dupModalOpen && (
        <DuplicatesModal
          params={dupParams}
          onClose={() => setDupModalOpen(false)}
          onChanged={() => void queryClient.invalidateQueries({ queryKey: ['duplicates'] })}
        />
      )}
    </div>
  )
}
