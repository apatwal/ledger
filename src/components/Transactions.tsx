import { useEffect, useState, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, Search, ChevronLeft, ChevronRight, AlertCircle, Sparkles, EyeOff, ChevronDown, Check } from 'lucide-react'
import { getTransactions, deleteTransaction, getCategories, getAssistantStatus, categorizeBatch, createRule, applyRules, getDuplicates } from '../lib/api'
import type { Transaction, TransactionQuery } from '../lib/types'
import TransactionModal from './TransactionModal'
import DatePicker from './DatePicker'
import { todayISO, toISO, addDays } from '../lib/date'
import { useAccountSelection } from '../lib/accountSelection'
import { acctKey, invalidateLedger } from '../lib/queryKeys'
import { iconForCategory } from '../lib/categoryIcons'

// Merchant logo → rounded avatar, falling back to the category glyph when the
// logo is missing or fails to load.
function MerchantAvatar({ tx }: { tx: Transaction }) {
  const [broken, setBroken] = useState(false)
  const Icon = iconForCategory(tx.category)
  if (tx.logo_url && !broken) {
    return (
      <img
        className="merchant-logo"
        src={tx.logo_url}
        alt=""
        loading="lazy"
        onError={() => setBroken(true)}
      />
    )
  }
  return (
    <span className="merchant-logo merchant-logo-fallback" aria-hidden="true">
      <Icon size={13} />
    </span>
  )
}

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

// ─── Exclude filter (v10) ──────────────────────────────────────────────────
// Lets the ledger hide whole transaction TYPES and/or CATEGORIES. The checked
// sets are sent as `exclude_types` / `exclude_categories` so the server hides
// them across pagination, and persisted to localStorage so the choice sticks.
const EXCLUDE_TYPES_KEY = 'expense.excludeTypes'
const EXCLUDE_CATEGORIES_KEY = 'expense.excludeCategories'

const TYPE_OPTIONS: { value: 'income' | 'expense' | 'transfer' | 'refund'; label: string }[] = [
  { value: 'income', label: 'Income' },
  { value: 'expense', label: 'Expense' },
  { value: 'transfer', label: 'Transfer' },
  { value: 'refund', label: 'Refund' },
]

function loadExcluded(key: string): Set<string> {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return new Set()
    const arr = JSON.parse(raw) as unknown
    return new Set(Array.isArray(arr) ? arr.filter((x): x is string => typeof x === 'string') : [])
  } catch {
    return new Set()
  }
}

function saveExcluded(key: string, set: Set<string>) {
  try {
    localStorage.setItem(key, JSON.stringify([...set]))
  } catch {
    /* storage unavailable — exclusions just won't persist */
  }
}

// Dropdown that mirrors the account multi-select: check items to EXCLUDE them.
function ExcludeFilter({
  categories,
  excludedTypes,
  excludedCategories,
  onToggleType,
  onToggleCategory,
  onClear,
}: {
  categories: string[]
  excludedTypes: Set<string>
  excludedCategories: Set<string>
  onToggleType: (value: string) => void
  onToggleCategory: (value: string) => void
  onClear: () => void
}) {
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

  const count = excludedTypes.size + excludedCategories.size
  const label = count === 0 ? 'Nothing hidden' : `${count} hidden`

  // "Income"/"Transfer" are Plaid category labels that duplicate transaction
  // TYPES, so they'd otherwise show twice in this popover (once per section).
  // Drop any category that collides with a type label — the Types row already
  // hides those rows.
  const typeLabels = new Set(TYPE_OPTIONS.map((t) => t.label.toLowerCase()))
  const excludableCategories = categories.filter((c) => !typeLabels.has(c.toLowerCase()))

  return (
    <div className="exclude-filter" ref={rootRef}>
      <button
        type="button"
        className={`exclude-trigger${open ? ' open' : ''}`}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Hide certain transaction types or categories from the ledger"
      >
        <EyeOff size={14} />
        <span className="exclude-trigger-label">{label}</span>
        {count > 0 && <span className="exclude-count">{count}</span>}
        <ChevronDown size={14} className="exclude-caret" />
      </button>

      {open && (
        <div className="exclude-popover" role="listbox" aria-multiselectable="true">
          <div className="exclude-actions">
            <span className="exclude-hint">Check to hide</span>
            {count > 0 && (
              <>
                <span className="exclude-dot">·</span>
                <button type="button" className="exclude-action" onClick={onClear}>Clear</button>
              </>
            )}
          </div>

          <div className="exclude-group-label">Types</div>
          <div className="exclude-list">
            {TYPE_OPTIONS.map((t) => {
              const on = excludedTypes.has(t.value)
              return (
                <button
                  key={t.value}
                  type="button"
                  role="option"
                  aria-selected={on}
                  className={`exclude-row${on ? ' on' : ''}`}
                  onClick={() => onToggleType(t.value)}
                >
                  <span className={`exclude-check${on ? ' on' : ''}`}>
                    {on && <Check size={11} strokeWidth={3} />}
                  </span>
                  <span className="exclude-name">{t.label}</span>
                </button>
              )
            })}
          </div>

          {excludableCategories.length > 0 && (
            <>
              <div className="exclude-group-label">Categories</div>
              <div className="exclude-list">
                {excludableCategories.map((c) => {
                  const on = excludedCategories.has(c)
                  const Icon = iconForCategory(c)
                  return (
                    <button
                      key={c}
                      type="button"
                      role="option"
                      aria-selected={on}
                      className={`exclude-row${on ? ' on' : ''}`}
                      onClick={() => onToggleCategory(c)}
                    >
                      <span className={`exclude-check${on ? ' on' : ''}`}>
                        {on && <Check size={11} strokeWidth={3} />}
                      </span>
                      <Icon size={14} className="exclude-row-icon" />
                      <span className="exclude-name">{c}</span>
                    </button>
                  )
                })}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default function Transactions() {
  const queryClient = useQueryClient()
  const [actionError, setActionError] = useState<string | null>(null)

  // Which accounts are in view is driven globally by the header selector.
  const { accountsParam, allSelected, selected } = useAccountSelection()
  const accountsSel = accountsParam()
  const acctPart = acctKey(accountsSel)

  const [offset, setOffset] = useState(0)

  const [searchParams] = useSearchParams()

  // Filters
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState<'' | 'income' | 'expense' | 'transfer' | 'refund'>('')
  const [categoryFilter, setCategoryFilter] = useState('')
  // '' = all, 'true' = only needs-review (deep-linkable via ?needs_review=true)
  const [reviewFilter, setReviewFilter] = useState<'' | 'true'>(searchParams.get('needs_review') === 'true' ? 'true' : '')
  const [startDate, setStartDate] = useState(DEFAULT_START)
  const [endDate, setEndDate] = useState(DEFAULT_END)

  // Exclude filter (v10) — hide whole types/categories server-side. Restored
  // from localStorage on mount and persisted on change (like the account picker).
  const [excludeTypes, setExcludeTypes] = useState<Set<string>>(() => loadExcluded(EXCLUDE_TYPES_KEY))
  const [excludeCategories, setExcludeCategories] = useState<Set<string>>(() => loadExcluded(EXCLUDE_CATEGORIES_KEY))
  // Stable primitive keys for effect deps (Sets are re-created each toggle).
  const excludeTypesKey = [...excludeTypes].sort().join(',')
  const excludeCategoriesKey = [...excludeCategories].sort().join(',')

  // Duplicate detection (v7) — `dupOnly` is a client-side quick filter over ids
  // of rows that belong to a flagged duplicate group.
  const [dupOnly, setDupOnly] = useState(false)

  // AI batch categorize
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

  // Build the current query. Every param that changes the result set is also
  // encoded into the query key below, so cached pages are never shown for the
  // wrong filter.
  const query: TransactionQuery = {
    limit: PAGE_SIZE + 1,
    offset: offset * PAGE_SIZE,
  }
  if (typeFilter) query.type = typeFilter
  if (categoryFilter) query.category = categoryFilter
  if (accountsSel) query.accounts = accountsSel
  if (reviewFilter === 'true') query.needs_review = true
  if (startDate) query.start_date = startDate
  if (endDate) query.end_date = endDate
  if (excludeTypes.size) query.exclude_types = [...excludeTypes]
  if (excludeCategories.size) query.exclude_categories = [...excludeCategories]

  const listQuery = useQuery({
    queryKey: ['transactions', 'list', {
      type: typeFilter || null,
      category: categoryFilter || null,
      accounts: acctPart,
      needs_review: reviewFilter === 'true',
      start_date: startDate || null,
      end_date: endDate || null,
      exclude_types: excludeTypesKey,
      exclude_categories: excludeCategoriesKey,
      limit: PAGE_SIZE + 1,
      offset: offset * PAGE_SIZE,
    }],
    queryFn: () => getTransactions(query),
  })
  const rawRows = listQuery.data ?? []
  const hasMore = rawRows.length > PAGE_SIZE
  const transactions = rawRows.slice(0, PAGE_SIZE)
  const loading = listQuery.isPending
  const error = actionError ?? (listQuery.error instanceof Error ? listQuery.error.message : listQuery.error ? 'Failed to load transactions' : null)

  // Duplicate groups for the current window/accounts, flattened to an id set.
  // Shares the ['duplicates'] prefix with the dashboard so writes refresh both.
  const dupIdsQuery = useQuery({
    queryKey: ['duplicates', 'ids', { start_date: startDate || null, end_date: endDate || null, accounts: acctPart }],
    queryFn: () => getDuplicates({
      ...(accountsSel ? { accounts: accountsSel } : {}),
      ...(startDate ? { start_date: startDate } : {}),
      ...(endDate ? { end_date: endDate } : {}),
    }),
  })
  const dupIds = new Set((dupIdsQuery.data ?? []).flatMap((g) => g.transactions.map((t) => t.id)))

  const { data: categories = [] } = useQuery({ queryKey: ['categories'], queryFn: getCategories })
  const { data: assistantStatus } = useQuery({ queryKey: ['assistant', 'status'], queryFn: getAssistantStatus })
  const aiEnabled = assistantStatus?.enabled ?? false

  // Persist exclusions whenever they change.
  useEffect(() => { saveExcluded(EXCLUDE_TYPES_KEY, excludeTypes) }, [excludeTypes])
  useEffect(() => { saveExcluded(EXCLUDE_CATEGORIES_KEY, excludeCategories) }, [excludeCategories])

  async function handleAutoCategorize() {
    setCategorizing(true)
    setAiMsg(null)
    try {
      // Target the current view's window; only uncategorized/needs-review rows.
      // The batch endpoint takes a single account, so scope to it only when the
      // global selection has narrowed to exactly one.
      const singleAccount = !allSelected && selected.length === 1 ? selected[0] : undefined
      const { results } = await categorizeBatch({
        only_uncategorized: true,
        ...(singleAccount ? { account: singleAccount } : {}),
        ...(startDate ? { start_date: startDate } : {}),
        ...(endDate ? { end_date: endDate } : {}),
      })
      setAiMsg(`AI categorized ${results.length} transaction${results.length === 1 ? '' : 's'}.`)
      void queryClient.invalidateQueries({ queryKey: ['transactions'] })
      void queryClient.invalidateQueries({ queryKey: ['stats'] })
    } catch (e) {
      setAiMsg(e instanceof Error ? e.message : 'Auto-categorize failed')
    } finally {
      setCategorizing(false)
    }
  }

  // v5.1 — resolve a brokerage row: create a rule (savings=Investment expense, or keep transfer) + apply.
  async function handleBrokerageChoice(tx: Transaction, choice: 'savings' | 'transfer') {
    setBrokerageBusyId(tx.id)
    setActionError(null)
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
      void queryClient.invalidateQueries({ queryKey: ['rules'] })
      void queryClient.invalidateQueries({ queryKey: ['transactions'] })
      void queryClient.invalidateQueries({ queryKey: ['stats'] })
      void queryClient.invalidateQueries({ queryKey: ['duplicates'] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Failed to save your choice')
    } finally {
      setBrokerageBusyId(null)
    }
  }

  // Toggle a single type/category in the exclude set. Reset to page 1 so the
  // narrower result set doesn't leave you stranded on an empty page.
  function toggleExcludeType(value: string) {
    setExcludeTypes((prev) => {
      const next = new Set(prev)
      if (next.has(value)) next.delete(value)
      else next.add(value)
      return next
    })
    setOffset(0)
  }

  function toggleExcludeCategory(value: string) {
    setExcludeCategories((prev) => {
      const next = new Set(prev)
      if (next.has(value)) next.delete(value)
      else next.add(value)
      return next
    })
    setOffset(0)
  }

  function clearExclusions() {
    setExcludeTypes(new Set())
    setExcludeCategories(new Set())
    setOffset(0)
  }

  function handleApplyFilters() {
    // Filters are already live (they're in the query key); resetting to page 1
    // keeps the narrower result set from stranding you on an empty page.
    setOffset(0)
  }

  function handleClearFilters() {
    setTypeFilter('')
    setCategoryFilter('')
    setReviewFilter('')
    setStartDate(DEFAULT_START)
    setEndDate(DEFAULT_END)
    setSearch('')
    setDupOnly(false)
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
    setActionError(null)
    try {
      await deleteTransaction(id)
      setDeleteConfirmId(null)
      invalidateLedger(queryClient)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setDeletingId(null)
    }
  }

  function handleSaved() {
    // TransactionModal invalidates the ledger on save; just close here.
    setModalOpen(false)
    setEditingTx(null)
  }

  // Client-side search filter (description/category/date match)
  const searched = search.trim()
    ? transactions.filter((t) =>
        t.category.toLowerCase().includes(search.toLowerCase()) ||
        (t.description?.toLowerCase() ?? '').includes(search.toLowerCase()) ||
        (t.account?.toLowerCase() ?? '').includes(search.toLowerCase()) ||
        t.date.includes(search)
      )
    : transactions
  // "Duplicates only" quick filter (client-side; scoped to the loaded page)
  const filtered = dupOnly ? searched.filter((t) => dupIds.has(t.id)) : searched

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
            <label htmlFor="f-review">Review</label>
            <select id="f-review" value={reviewFilter} onChange={(e) => setReviewFilter(e.target.value as '' | 'true')}>
              <option value="">All rows</option>
              <option value="true">Needs review only</option>
            </select>
          </div>
          {dupIds.size > 0 && (
            <div>
              <label htmlFor="f-dup">Duplicates</label>
              <select
                id="f-dup"
                value={dupOnly ? 'true' : ''}
                onChange={(e) => setDupOnly(e.target.value === 'true')}
              >
                <option value="">All rows</option>
                <option value="true">Duplicates only</option>
              </select>
            </div>
          )}
          <div>
            <label>Exclude</label>
            <ExcludeFilter
              categories={categories}
              excludedTypes={excludeTypes}
              excludedCategories={excludeCategories}
              onToggleType={toggleExcludeType}
              onToggleCategory={toggleExcludeCategory}
              onClear={clearExclusions}
            />
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
                      {dupIds.has(tx.id) && (
                        <span
                          className="badge badge-duplicate"
                          title="This charge matches another with the same date, amount, merchant and account"
                        >
                          duplicate
                        </span>
                      )}
                      {tx.pending && (
                        <span
                          className="badge badge-pending"
                          title="This charge is still pending and may change or disappear"
                        >
                          pending
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
                  <td>
                    {(() => {
                      const CatIcon = iconForCategory(tx.category)
                      return (
                        <span className="cat-cell">
                          <CatIcon size={14} className="cat-cell-icon" />
                          {tx.category}
                        </span>
                      )
                    })()}
                  </td>
                  <td>
                    {tx.account
                      ? <span className="badge badge-account">{tx.account}</span>
                      : <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>Unassigned</span>}
                  </td>
                  <td style={{ maxWidth: 240 }}>
                    <span className="merchant-cell">
                      <MerchantAvatar tx={tx} />
                      <span className="merchant-cell-text" title={tx.merchant_name ?? tx.description ?? undefined}>
                        {tx.merchant_name ?? tx.description ?? <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>—</span>}
                      </span>
                    </span>
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
