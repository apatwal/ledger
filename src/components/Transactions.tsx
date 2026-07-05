import { useEffect, useState, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Plus, Pencil, Trash2, Search, ChevronLeft, ChevronRight, AlertCircle, Sparkles } from 'lucide-react'
import { getTransactions, deleteTransaction, getCategories, getAccounts, getAssistantStatus, categorizeBatch, createRule, applyRules } from '../lib/api'
import type { Transaction, TransactionQuery } from '../lib/types'
import TransactionModal from './TransactionModal'
import DatePicker from './DatePicker'
import { todayISO, toISO, addDays } from '../lib/date'

// Default filter window: the last 30 days (matches the Dashboard default).
const DEFAULT_END = todayISO()
const DEFAULT_START = toISO(addDays(new Date(), -30))

// A brokerage review row's reason looks like:
//   "Brokerage: count as savings or keep as transfer? (fidelity)"
function isBrokerageReview(t: Transaction): boolean {
  return t.needs_review && (t.review_reason?.startsWith('Brokerage:') ?? false)
}

// Prefer the token in parens; fall back to the longest word in the description.
function brokerageToken(t: Transaction): string {
  const m = /\(([^)]+)\)\s*$/.exec(t.review_reason ?? '')
  if (m && m[1].trim()) return m[1].trim().toLowerCase()
  const words = (t.description ?? '').toLowerCase().match(/[a-z][a-z'&-]{2,}/g) ?? []
  return words.reduce((longest, w) => (w.length > longest.length ? w : longest), (t.description ?? '').trim().toLowerCase())
}

const PAGE_SIZE = 20

function fmt(n: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n)
}

export default function Transactions() {
  const [transactions, setTransactions] = useState<Transaction[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [accounts, setAccounts] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)

  const [searchParams] = useSearchParams()

  // Filters
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState<'' | 'income' | 'expense' | 'transfer' | 'refund'>('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [accountFilter, setAccountFilter] = useState('')
  // '' = all, 'true' = only needs-review (deep-linkable via ?needs_review=true)
  const [reviewFilter, setReviewFilter] = useState<'' | 'true'>(searchParams.get('needs_review') === 'true' ? 'true' : '')
  const [startDate, setStartDate] = useState(DEFAULT_START)
  const [endDate, setEndDate] = useState(DEFAULT_END)

  // AI batch categorize
  const [aiEnabled, setAiEnabled] = useState(false)
  const [categorizing, setCategorizing] = useState(false)
  const [aiMsg, setAiMsg] = useState<string | null>(null)

  // Brokerage savings-vs-transfer choice (v5.1)
  const [brokerageBusyId, setBrokerageBusyId] = useState<number | null>(null)

  // Modal
  const [modalOpen, setModalOpen] = useState(false)
  const [editingTx, setEditingTx] = useState<Transaction | null>(null)

  // Delete confirm
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null)

  const load = useCallback(async (page: number) => {
    setLoading(true)
    setError(null)
    const query: TransactionQuery = {
      limit: PAGE_SIZE + 1,
      offset: page * PAGE_SIZE,
    }
    if (typeFilter) query.type = typeFilter
    if (categoryFilter) query.category = categoryFilter
    if (accountFilter) query.account = accountFilter
    if (reviewFilter === 'true') query.needs_review = true
    if (startDate) query.start_date = startDate
    if (endDate) query.end_date = endDate

    try {
      const rows = await getTransactions(query)
      setHasMore(rows.length > PAGE_SIZE)
      setTransactions(rows.slice(0, PAGE_SIZE))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load transactions')
    } finally {
      setLoading(false)
    }
  }, [typeFilter, categoryFilter, accountFilter, reviewFilter, startDate, endDate])

  useEffect(() => {
    void load(offset)
  }, [load, offset])

  useEffect(() => {
    getCategories()
      .then(setCategories)
      .catch(() => { /* ignore */ })
    getAccounts()
      .then(setAccounts)
      .catch(() => { /* ignore */ })
    getAssistantStatus()
      .then((s) => setAiEnabled(s.enabled))
      .catch(() => setAiEnabled(false))
  }, [])

  async function handleAutoCategorize() {
    setCategorizing(true)
    setAiMsg(null)
    try {
      // Target the current view's window/account; only uncategorized/needs-review rows.
      const { results } = await categorizeBatch({
        only_uncategorized: true,
        ...(accountFilter ? { account: accountFilter } : {}),
        ...(startDate ? { start_date: startDate } : {}),
        ...(endDate ? { end_date: endDate } : {}),
      })
      setAiMsg(`AI categorized ${results.length} transaction${results.length === 1 ? '' : 's'}.`)
      void load(offset)
    } catch (e) {
      setAiMsg(e instanceof Error ? e.message : 'Auto-categorize failed')
    } finally {
      setCategorizing(false)
    }
  }

  // v5.1 — resolve a brokerage row: create a rule (savings=Investment expense, or keep transfer) + apply.
  async function handleBrokerageChoice(tx: Transaction, choice: 'savings' | 'transfer') {
    setBrokerageBusyId(tx.id)
    setError(null)
    const token = brokerageToken(tx)
    try {
      await createRule({
        name: `Brokerage ${token} → ${choice === 'savings' ? 'savings' : 'transfer'}`,
        match_field: 'description',
        match_op: 'contains',
        match_value: token,
        set_type: choice === 'savings' ? 'expense' : 'transfer',
        set_category: choice === 'savings' ? 'Investment' : null,
      })
      await applyRules({})
      void load(offset)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save your choice')
    } finally {
      setBrokerageBusyId(null)
    }
  }

  function handleApplyFilters() {
    setOffset(0)
    void load(0)
  }

  function handleClearFilters() {
    setTypeFilter('')
    setCategoryFilter('')
    setAccountFilter('')
    setReviewFilter('')
    setStartDate(DEFAULT_START)
    setEndDate(DEFAULT_END)
    setSearch('')
    setOffset(0)
  }

  function openAdd() {
    setEditingTx(null)
    setModalOpen(true)
  }

  function openEdit(tx: Transaction) {
    setEditingTx(tx)
    setModalOpen(true)
  }

  async function handleDelete(id: number) {
    setDeletingId(id)
    try {
      await deleteTransaction(id)
      setDeleteConfirmId(null)
      void load(offset)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setDeletingId(null)
    }
  }

  function handleSaved() {
    setModalOpen(false)
    setEditingTx(null)
    void load(offset)
  }

  // Client-side search filter (description/category/date match)
  const filtered = search.trim()
    ? transactions.filter((t) =>
        t.category.toLowerCase().includes(search.toLowerCase()) ||
        (t.description?.toLowerCase() ?? '').includes(search.toLowerCase()) ||
        (t.account?.toLowerCase() ?? '').includes(search.toLowerCase()) ||
        t.date.includes(search)
      )
    : transactions

  return (
    <div>
      <div className="page-header flex justify-between items-center" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <div className="page-eyebrow">The ledger</div>
          <h1 className="page-title">Transactions</h1>
          <p className="page-subtitle">Every entry, posted and accounted for.</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {aiEnabled && (
            <button className="btn btn-secondary" onClick={() => void handleAutoCategorize()} disabled={categorizing}>
              {categorizing ? <span className="spinner" style={{ width: 14, height: 14 }} /> : <Sparkles size={15} />}
              Auto-categorize with AI
            </button>
          )}
          <button className="btn btn-primary" onClick={openAdd}>
            <Plus size={16} />
            Add entry
          </button>
        </div>
      </div>

      {aiMsg && (
        <div className="card card-sm" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Sparkles size={15} style={{ color: 'var(--gold)' }} />
          {aiMsg}
        </div>
      )}

      {/* Filters */}
      <div className="card card-sm" style={{ marginBottom: 20 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12 }}>
          <div>
            <label htmlFor="f-type">Type</label>
            <select id="f-type" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value as '' | 'income' | 'expense' | 'transfer' | 'refund')}>
              <option value="">All types</option>
              <option value="income">Income</option>
              <option value="expense">Expense</option>
              <option value="transfer">Transfer</option>
              <option value="refund">Refund</option>
            </select>
          </div>
          <div>
            <label htmlFor="f-cat">Category</label>
            <select id="f-cat" value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
              <option value="">All categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="f-account">Account / Card</label>
            <select id="f-account" value={accountFilter} onChange={(e) => setAccountFilter(e.target.value)}>
              <option value="">All cards</option>
              {accounts.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="f-review">Review</label>
            <select id="f-review" value={reviewFilter} onChange={(e) => setReviewFilter(e.target.value as '' | 'true')}>
              <option value="">All rows</option>
              <option value="true">Needs review only</option>
            </select>
          </div>
          <div>
            <label htmlFor="f-start">From date</label>
            <DatePicker
              id="f-start"
              ariaLabel="From date"
              block
              placeholder="Any start"
              value={startDate}
              max={endDate || undefined}
              onChange={setStartDate}
            />
          </div>
          <div>
            <label htmlFor="f-end">To date</label>
            <DatePicker
              id="f-end"
              ariaLabel="To date"
              block
              placeholder="Any end"
              value={endDate}
              min={startDate || undefined}
              onChange={setEndDate}
            />
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
          <button className="btn btn-primary btn-sm" onClick={handleApplyFilters}>Apply filters</button>
          <button className="btn btn-secondary btn-sm" onClick={handleClearFilters}>Clear</button>
        </div>
      </div>

      {/* Search */}
      <div style={{ position: 'relative', marginBottom: 16, maxWidth: 360 }}>
        <Search size={15} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
        <input
          type="text"
          placeholder="Search category, description, date…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ paddingLeft: 36 }}
        />
      </div>

      {/* Error */}
      {error && (
        <div className="error-state" style={{ padding: 24 }}>
          <AlertCircle size={24} />
          {error}
        </div>
      )}

      {/* Table */}
      <div className="table-wrapper">
        {loading ? (
          <div className="loading-state">
            <div className="spinner spinner-lg" />
            Loading transactions…
          </div>
        ) : filtered.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">
              <Plus size={40} />
            </div>
            <div>The ledger is empty for these filters.</div>
            <button className="btn btn-primary btn-sm" onClick={openAdd}>
              Post your first entry
            </button>
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Type</th>
                <th>Category</th>
                <th>Account</th>
                <th>Description</th>
                <th style={{ textAlign: 'right' }}>Amount</th>
                <th>Source</th>
                <th style={{ textAlign: 'center' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((tx) => (
                <tr key={tx.id}>
                  <td className="mono" style={{ color: 'var(--text-secondary)' }}>
                    {tx.date}
                  </td>
                  <td>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                      <span className={`badge badge-${tx.type}`}>
                        {tx.type}
                      </span>
                      {tx.type === 'transfer' && (
                        <span
                          className="badge badge-excluded"
                          title="Transfers move money between your own accounts and are excluded from spending stats"
                        >
                          excluded from spend
                        </span>
                      )}
                      {tx.needs_review && !isBrokerageReview(tx) && (
                        <span
                          className="badge badge-needs-review"
                          title={tx.review_reason ?? 'This row needs review'}
                        >
                          needs review
                        </span>
                      )}
                    </div>
                    {isBrokerageReview(tx) && (
                      <div className="brokerage-choice">
                        <div className="brokerage-choice-label">Brokerage deposit — how should it count?</div>
                        <div className="brokerage-choice-btns">
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => void handleBrokerageChoice(tx, 'savings')}
                            disabled={brokerageBusyId === tx.id}
                          >
                            {brokerageBusyId === tx.id ? <span className="spinner" style={{ width: 12, height: 12 }} /> : null}
                            Count as savings
                          </button>
                          <button
                            className="btn btn-ghost btn-sm"
                            onClick={() => void handleBrokerageChoice(tx, 'transfer')}
                            disabled={brokerageBusyId === tx.id}
                          >
                            Keep as transfer
                          </button>
                        </div>
                        <div className="brokerage-choice-help">
                          Counting as savings adds it to your savings rate; transfer keeps it neutral.
                        </div>
                      </div>
                    )}
                  </td>
                  <td>{tx.category}</td>
                  <td>
                    {tx.account
                      ? <span className="badge badge-account">{tx.account}</span>
                      : <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>Unassigned</span>}
                  </td>
                  <td style={{ color: 'var(--text-muted)', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {tx.description ?? <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>—</span>}
                  </td>
                  <td className="num" style={{ fontWeight: 600 }}>
                    <span
                      className={
                        tx.type === 'income' ? 'text-income'
                        : tx.type === 'expense' ? 'text-expense'
                        : tx.type === 'refund' ? 'text-income'
                        : 'text-transfer'
                      }
                    >
                      {tx.type === 'income' ? '+' : tx.type === 'expense' ? '−' : tx.type === 'refund' ? '−' : ''}{fmt(tx.amount)}
                    </span>
                    {tx.type === 'refund' && (
                      <span className="amount-tag" title="A refund nets against spending in its category">credit</span>
                    )}
                  </td>
                  <td>
                    <span className={`badge badge-${tx.source}`}>
                      {tx.source}
                    </span>
                  </td>
                  <td>
                    <div style={{ display: 'flex', gap: 6, justifyContent: 'center' }}>
                      <button
                        className="btn btn-ghost btn-icon btn-sm"
                        onClick={() => openEdit(tx)}
                        title="Edit"
                      >
                        <Pencil size={14} />
                      </button>
                      {deleteConfirmId === tx.id ? (
                        <>
                          <button
                            className="btn btn-danger btn-sm"
                            onClick={() => void handleDelete(tx.id)}
                            disabled={deletingId === tx.id}
                          >
                            {deletingId === tx.id ? (
                              <span className="spinner" style={{ width: 12, height: 12 }} />
                            ) : 'Confirm'}
                          </button>
                          <button
                            className="btn btn-ghost btn-sm"
                            onClick={() => setDeleteConfirmId(null)}
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <button
                          className="btn btn-ghost btn-icon btn-sm"
                          onClick={() => setDeleteConfirmId(tx.id)}
                          title="Delete"
                          style={{ color: 'var(--danger)' }}
                        >
                          <Trash2 size={14} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {/* Pagination */}
        {!loading && filtered.length > 0 && (
          <div className="pagination">
            <span className="pagination-info">
              Page {offset + 1} · {filtered.length} rows
            </span>
            <div className="pagination-controls">
              <button
                className="btn btn-secondary btn-icon btn-sm"
                onClick={() => setOffset((o) => Math.max(0, o - 1))}
                disabled={offset === 0}
              >
                <ChevronLeft size={14} />
              </button>
              <button
                className="btn btn-secondary btn-icon btn-sm"
                onClick={() => setOffset((o) => o + 1)}
                disabled={!hasMore}
              >
                <ChevronRight size={14} />
              </button>
            </div>
          </div>
        )}
      </div>

      {modalOpen && (
        <TransactionModal
          transaction={editingTx}
          onClose={() => setModalOpen(false)}
          onSaved={handleSaved}
        />
      )}
    </div>
  )
}
