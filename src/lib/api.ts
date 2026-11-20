import type {
  Transaction,
  TransactionCreate,
  TransactionUpdate,
  TransactionQuery,
  StatsSummary,
  StatsByCategory,
  StatsOverTime,
  CsvImportResult,
  HealthResponse,
  Granularity,
  AccountStat,
  ChatMessage,
  AssistantReply,
  CategorySuggestion,
  InsightsResult,
  AssistantStatus,
  Rule,
  RuleCreate,
  RuleUpdate,
  ApplyRulesResult,
  PreviewRuleResult,
  CategorizeBatchResult,
  ImportBatch,
  ReassignImportResult,
  DuplicateGroup,
  DismissDuplicatesResult,
  PlaidStatus,
  PlaidItem,
  PlaidSyncResult,
  Holding,
  CategoryBudget,
  CategoryBudgetInput,
  CategoryBudgetUpdate,
  SavingsGoal,
  SavingsGoalInput,
  SavingsGoalUpdate,
  BudgetChatResponse,
  AccountNickname,
  NetWorth,
  Snapshot,
  InvestmentFlowPoint,
} from './types'

const BASE = '/api'

// ─── Auth (Clerk) ──────────────────────────────────────────────────────────────
// The Clerk session-token getter is registered at runtime by <AuthGate> (which has
// React context via useAuth). When set, every request carries `Authorization:
// Bearer <token>`. When unset (Clerk not configured), requests go out unauthenticated
// and the gated backend behaves exactly as before.
let authTokenGetter: (() => Promise<string | null>) | null = null

export function setAuthTokenGetter(fn: (() => Promise<string | null>) | null): void {
  authTokenGetter = fn
}

// Injects the bearer token when available. Every call goes through send(), below.
async function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers as HeadersInit | undefined)
  if (authTokenGetter) {
    try {
      const token = await authTokenGetter()
      if (token) headers.set('Authorization', `Bearer ${token}`)
    } catch {
      // Token fetch failed — let the request proceed; the backend will 401 if it must.
    }
  }
  return fetch(url, { ...init, headers })
}

// A query value can be a scalar, or a string[] (serialized comma-joined, e.g. `accounts`).
type QueryValue = string | number | boolean | string[] | undefined
type QueryParams = Record<string, QueryValue>

// ─── Errors ──────────────────────────────────────────────────────────────────
// Every failed request throws an ApiError. `.message` is always a human sentence
// (the server's `detail`, or a plain-English fallback), so callers that only
// render `error.message` read fine; <ErrorNotice> uses the other fields for its
// collapsible Details block.
export class ApiError extends Error {
  /** HTTP status, or null when the request never got a response (network down). */
  readonly status: number | null
  readonly method: string
  /** Request path including the query string, e.g. `/api/transactions?limit=51`. */
  readonly path: string
  /** Server-issued ID for an unhandled 500 — search Render's logs for it. */
  readonly errorId: string | null
  /** Exception class name for an unhandled 500 (e.g. `ResponseValidationError`). */
  readonly type: string | null
  /** The underlying reason in technical terms, for the Details block. */
  readonly cause: string | null
  /** When it failed — handy for lining the error up with the server log. */
  readonly at: Date

  constructor(
    message: string,
    init: {
      status: number | null
      method: string
      path: string
      errorId?: string | null
      type?: string | null
      cause?: string | null
    },
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = init.status
    this.method = init.method
    this.path = init.path
    this.errorId = init.errorId ?? null
    this.type = init.type ?? null
    this.cause = init.cause ?? null
    this.at = new Date()
  }

  /** True when fetch itself failed — no HTTP response at all. */
  get isNetwork(): boolean {
    return this.status === null
  }

  /** An unhandled server crash (carries an error ID), as opposed to a deliberate HTTPException. */
  get isServerCrash(): boolean {
    return this.errorId !== null || this.type !== null
  }
}

export const NETWORK_ERROR_MESSAGE = "Can't reach the server. Check your connection, or try again in a moment."

// Reason phrases for when res.statusText is empty — it always is over HTTP/2
// (Render), which is how "500 : Internal Server Error" used to reach the screen.
const REASON: Record<number, string> = {
  400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden', 404: 'Not Found',
  405: 'Method Not Allowed', 408: 'Request Timeout', 409: 'Conflict', 413: 'Payload Too Large',
  422: 'Unprocessable Content', 429: 'Too Many Requests', 500: 'Internal Server Error',
  502: 'Bad Gateway', 503: 'Service Unavailable', 504: 'Gateway Timeout',
}

/** "500 Internal Server Error" — never "500 " with an empty reason. */
export function statusLabel(status: number, statusText = ''): string {
  const reason = statusText || REASON[status] || ''
  return reason ? `${status} ${reason}` : String(status)
}

// Body text for the Details block when the server didn't send JSON (e.g. a proxy
// error page). Strip tags and squash whitespace so HTML doesn't swamp the panel.
function snippet(text: string): string | null {
  const plain = text.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim()
  if (!plain) return null
  return plain.length > 300 ? `${plain.slice(0, 299)}…` : plain
}

// FastAPI puts the human-readable text in `detail` — a plain string, an object
// when the route attaches extra context (e.g. the already-linked-bank 409 carries
// the existing item_id), or a list for a 422. An unhandled 500 also carries a
// structured `error` object (see src/api/errors.py).
async function toApiError(res: Response, method: string, path: string): Promise<ApiError> {
  const text = await res.text().catch(() => '')
  let body: { detail?: unknown; error?: unknown } | null = null
  try {
    body = JSON.parse(text) as { detail?: unknown; error?: unknown }
  } catch {
    // not JSON — handled below
  }

  const detail = body?.detail
  let message: string | null = null
  if (typeof detail === 'string' && detail.trim()) {
    message = detail
  } else if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const msg = (detail as { message?: unknown }).message
    if (typeof msg === 'string' && msg.trim()) message = msg
  } else if (Array.isArray(detail) && detail.length) {
    // 422 RequestValidationError: [{loc, msg, ...}] — show the first one.
    const first = detail[0] as { loc?: unknown[]; msg?: unknown }
    const field = Array.isArray(first?.loc) ? first.loc.filter((p) => p !== 'body').join('.') : ''
    if (typeof first?.msg === 'string') message = field ? `${field}: ${first.msg}` : first.msg
  }

  const err = body?.error as
    | { id?: unknown; type?: unknown; message?: unknown }
    | undefined
  const str = (v: unknown) => (typeof v === 'string' && v ? v : null)
  const type = err ? str(err.type) : null
  const errMessage = err ? str(err.message) : null

  return new ApiError(message ?? `The server responded ${statusLabel(res.status, res.statusText)}.`, {
    status: res.status,
    method,
    path,
    errorId: err ? str(err.id) : null,
    type,
    cause: type && errMessage
      ? `${type}: ${errMessage}`
      : message ?? (body ? snippet(JSON.stringify(body)) : snippet(text)),
  })
}

// The one place a request goes out. Throws ApiError for a non-2xx response and
// for a network failure; returns the Response otherwise.
async function send(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase()
  const fullPath = `${BASE}${path}`
  let res: Response
  try {
    res = await authFetch(fullPath, init)
  } catch (e) {
    throw new ApiError(NETWORK_ERROR_MESSAGE, {
      status: null,
      method,
      path: fullPath,
      cause: e instanceof Error ? `${e.name}: ${e.message}` : String(e),
    })
  }
  if (!res.ok) throw await toApiError(res, method, fullPath)
  return res
}

// JSON request → parsed JSON response.
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await send(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers as Record<string, string> | undefined) },
  })
  try {
    return (await res.json()) as T
  } catch (e) {
    throw new ApiError('The server sent a response the app could not read.', {
      status: res.status,
      method: (init.method ?? 'GET').toUpperCase(),
      path: `${BASE}${path}`,
      cause: e instanceof Error ? `${e.name}: ${e.message}` : String(e),
    })
  }
}

// Request whose response body is ignored (DELETEs, 204s).
async function requestVoid(path: string, init: RequestInit = {}): Promise<void> {
  await send(path, init)
}

function toQuery(params: QueryParams): string {
  const q = new URLSearchParams()
  for (const [key, val] of Object.entries(params)) {
    if (val === undefined || val === null || val === '') continue
    if (Array.isArray(val)) {
      // `accounts` and friends → comma-joined; empty arrays are omitted (means "all").
      if (val.length) q.set(key, val.join(','))
    } else {
      q.set(key, String(val))
    }
  }
  const s = q.toString()
  return s ? `?${s}` : ''
}

// ─── Transactions ─────────────────────────────────────────────────────────────

export async function getTransactions(query?: TransactionQuery): Promise<Transaction[]> {
  // `accounts`, `exclude_types` and `exclude_categories` are string[] — toQuery
  // serializes them comma-joined and omits empty arrays.
  const qs = toQuery((query as QueryParams) ?? {})
  return request<Transaction[]>(`/transactions${qs}`)
}

export async function getTransaction(id: number): Promise<Transaction> {
  return request<Transaction>(`/transactions/${id}`)
}

export async function createTransaction(data: TransactionCreate): Promise<Transaction> {
  return request<Transaction>('/transactions', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateTransaction(id: number, data: TransactionUpdate): Promise<Transaction> {
  return request<Transaction>(`/transactions/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export async function deleteTransaction(id: number): Promise<void> {
  await requestVoid(`/transactions/${id}`, { method: 'DELETE' })
}

// ─── CSV ─────────────────────────────────────────────────────────────────────

export async function importCsv(
  file: File,
  account?: string,
  statementType?: 'card' | 'bank',
): Promise<CsvImportResult> {
  const form = new FormData()
  form.append('file', file)
  if (account && account.trim()) form.append('account', account.trim())
  if (statementType) form.append('statement_type', statementType)
  // No JSON Content-Type: the browser sets the multipart boundary itself.
  const res = await send('/transactions/csv', { method: 'POST', body: form })
  return res.json() as Promise<CsvImportResult>
}

// ─── Import history (v5.2) ──────────────────────────────────────────────────────

export async function getImports(): Promise<ImportBatch[]> {
  return request<ImportBatch[]>('/imports')
}

export async function reassignImport(id: number, account: string | null): Promise<ReassignImportResult> {
  return request<ReassignImportResult>(`/imports/${id}/reassign`, {
    method: 'POST',
    body: JSON.stringify({ account: account && account.trim() ? account.trim() : null }),
  })
}

export async function deleteImport(id: number): Promise<void> {
  await requestVoid(`/imports/${id}`, { method: 'DELETE' })
}

// ─── Duplicate detection (v7) ────────────────────────────────────────────────

export async function getDuplicates(params?: {
  start_date?: string
  end_date?: string
  account?: string
  accounts?: string[]
}): Promise<DuplicateGroup[]> {
  const qs = toQuery((params as QueryParams) ?? {})
  return request<DuplicateGroup[]>(`/duplicates${qs}`)
}

export async function dismissDuplicates(ids: number[]): Promise<DismissDuplicatesResult> {
  return request<DismissDuplicatesResult>('/duplicates/dismiss', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })
}

// ─── Plaid bank sync (v8) ───────────────────────────────────────────────────

export async function getPlaidStatus(): Promise<PlaidStatus> {
  return request<PlaidStatus>('/plaid/status')
}

// Pass an itemId to get an UPDATE-mode token: it re-authenticates that existing
// bank and re-opens account selection (the fix for an item stuck on NO_ACCOUNTS)
// instead of linking a brand-new one. Omit it for the normal connect flow.
export async function createPlaidLinkToken(
  itemId?: number,
): Promise<{ link_token: string; expiration: string }> {
  return request<{ link_token: string; expiration: string }>('/plaid/link-token', {
    method: 'POST',
    body: JSON.stringify(itemId === undefined ? {} : { item_id: itemId }),
  })
}

export async function exchangePlaidPublicToken(public_token: string): Promise<PlaidItem> {
  return request<PlaidItem>('/plaid/exchange', {
    method: 'POST',
    body: JSON.stringify({ public_token }),
  })
}

export async function plaidSync(itemId?: number): Promise<PlaidSyncResult> {
  return request<PlaidSyncResult>('/plaid/sync', {
    method: 'POST',
    body: JSON.stringify(itemId === undefined ? {} : { item_id: itemId }),
  })
}

export async function getPlaidItems(): Promise<PlaidItem[]> {
  return request<PlaidItem[]>('/plaid/items')
}

// Investment holdings (v9). Throws on 503 when Plaid is unconfigured; [] when
// there are no investment accounts.
export async function getHoldings(): Promise<Holding[]> {
  return request<Holding[]>('/plaid/holdings')
}

export async function deletePlaidItem(id: number): Promise<void> {
  await requestVoid(`/plaid/items/${id}`, { method: 'DELETE' })
}

// ─── Stats ────────────────────────────────────────────────────────────────────

export interface DateRangeParams {
  start_date?: string
  end_date?: string
  account?: string
  accounts?: string[] // v9 — multi-account filter (comma-joined server-side)
}

export async function getStatsSummary(params?: DateRangeParams): Promise<StatsSummary> {
  const qs = toQuery((params as QueryParams) ?? {})
  return request<StatsSummary>(`/stats/summary${qs}`)
}

export async function getStatsByCategory(
  params?: DateRangeParams & { type?: 'income' | 'expense' }
): Promise<StatsByCategory[]> {
  const qs = toQuery((params as QueryParams) ?? {})
  return request<StatsByCategory[]>(`/stats/by-category${qs}`)
}

export async function getStatsOverTime(
  granularity: Granularity,
  params?: DateRangeParams
): Promise<StatsOverTime[]> {
  const qs = toQuery({
    granularity,
    ...((params as QueryParams) ?? {}),
  })
  return request<StatsOverTime[]>(`/stats/over-time${qs}`)
}

// Net worth / total balance across all accounts (assets − liabilities). May be
// absent while the backend endpoint is still coming up — callers handle errors.
export async function getNetWorth(): Promise<NetWorth> {
  return request<NetWorth>('/stats/net-worth')
}

// by-account is the per-card breakdown — it does NOT take an account filter,
// only the date range. Strip any stray account param.
export async function getStatsByAccount(params?: { start_date?: string; end_date?: string }): Promise<AccountStat[]> {
  const qs = toQuery({ start_date: params?.start_date, end_date: params?.end_date })
  return request<AccountStat[]>(`/stats/by-account${qs}`)
}

// ─── Categories & Accounts ──────────────────────────────────────────────────────

export async function getCategories(): Promise<string[]> {
  return request<string[]>('/categories')
}

export async function getAccounts(): Promise<string[]> {
  return request<string[]>('/accounts')
}

// ─── Account nicknames (investments-viz) ──────────────────────────────────────
// Display-only friendly names. Filtering/query params always use the raw label.

export async function getNicknames(): Promise<AccountNickname[]> {
  return request<AccountNickname[]>('/accounts/nicknames')
}

// Upsert a nickname. A blank nickname reverts/deletes it server-side. Uses
// requestVoid so an empty (204) response body doesn't trip JSON parsing.
export async function upsertNickname(label: string, nickname: string): Promise<void> {
  await requestVoid('/accounts/nicknames', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label, nickname }),
  })
}

// Mark (or unmark) an account as a savings destination. Returns the full,
// updated nickname list so callers can refresh the cache from the response.
export async function setSavingsAccount(label: string, is_savings: boolean): Promise<AccountNickname[]> {
  return request<AccountNickname[]>('/accounts/savings', {
    method: 'PUT',
    body: JSON.stringify({ label, is_savings }),
  })
}

export async function deleteNickname(label: string): Promise<void> {
  await requestVoid(`/accounts/nicknames/${encodeURIComponent(label)}`, { method: 'DELETE' })
}

// ─── Investments: snapshots & flows (investments-viz) ─────────────────────────

// Portfolio value snapshots. Omit `account` for portfolio-level points. May 503
// when Plaid is unconfigured; [] when nothing has been recorded yet.
export async function getSnapshots(account?: string): Promise<Snapshot[]> {
  const qs = toQuery({ account })
  return request<Snapshot[]>(`/investments/snapshots${qs}`)
}

// Records the current portfolio value as a new snapshot.
export async function captureSnapshot(): Promise<Snapshot> {
  return request<Snapshot>('/investments/snapshot', {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

// Contributions vs. dividends bucketed over time.
export async function getInvestmentFlows(
  granularity: Granularity = 'month',
  range?: { start_date?: string; end_date?: string },
): Promise<InvestmentFlowPoint[]> {
  const qs = toQuery({ granularity, ...((range as QueryParams) ?? {}) })
  return request<InvestmentFlowPoint[]>(`/stats/investment-flows${qs}`)
}

// ─── Health ───────────────────────────────────────────────────────────────────

export async function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/health')
}

// ─── AI assistant ───────────────────────────────────────────────────────────────

export async function getAssistantStatus(): Promise<AssistantStatus> {
  return request<AssistantStatus>('/assistant/status')
}

export async function assistantChat(messages: ChatMessage[]): Promise<AssistantReply> {
  return request<AssistantReply>('/assistant/chat', {
    method: 'POST',
    body: JSON.stringify({ messages }),
  })
}

export async function suggestCategory(payload: {
  description: string
  amount: number | null
  type: string
}): Promise<CategorySuggestion> {
  return request<CategorySuggestion>('/assistant/categorize', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function getInsights(params?: DateRangeParams): Promise<InsightsResult> {
  const qs = toQuery((params as QueryParams) ?? {})
  return request<InsightsResult>(`/assistant/insights${qs}`)
}

export async function categorizeBatch(payload: {
  ids?: number[]
  only_uncategorized?: boolean
  account?: string
  start_date?: string
  end_date?: string
}): Promise<CategorizeBatchResult> {
  return request<CategorizeBatchResult>('/assistant/categorize-batch', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

// ─── Rules engine (v5) ──────────────────────────────────────────────────────────

export async function getRules(enabled?: boolean): Promise<Rule[]> {
  const qs = enabled === undefined ? '' : `?enabled=${enabled}`
  return request<Rule[]>(`/rules${qs}`)
}

export async function createRule(data: RuleCreate): Promise<Rule> {
  return request<Rule>('/rules', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateRule(id: number, data: RuleUpdate): Promise<Rule> {
  return request<Rule>(`/rules/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export async function deleteRule(id: number): Promise<void> {
  await requestVoid(`/rules/${id}`, { method: 'DELETE' })
}

export async function bulkDeleteRules(ids: number[]): Promise<{ deleted: number }> {
  return request<{ deleted: number }>('/rules/bulk-delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  })
}

export async function applyRules(payload?: { account?: string; only_review?: boolean }): Promise<ApplyRulesResult> {
  return request<ApplyRulesResult>('/rules/apply', {
    method: 'POST',
    body: JSON.stringify(payload ?? {}),
  })
}

export async function previewRule(data: RuleCreate): Promise<PreviewRuleResult> {
  return request<PreviewRuleResult>('/rules/preview', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

// ─── Budget: category limits (v9d) ────────────────────────────────────────────

export async function getCategoryBudgets(): Promise<CategoryBudget[]> {
  return request<CategoryBudget[]>('/budgets/categories')
}

export async function createCategoryBudget(data: CategoryBudgetInput): Promise<CategoryBudget> {
  return request<CategoryBudget>('/budgets/categories', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateCategoryBudget(id: number, data: CategoryBudgetUpdate): Promise<CategoryBudget> {
  return request<CategoryBudget>(`/budgets/categories/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export async function deleteCategoryBudget(id: number): Promise<void> {
  await requestVoid(`/budgets/categories/${id}`, { method: 'DELETE' })
}

// ─── Budget: savings goals (v9d) ──────────────────────────────────────────────

export async function getSavingsGoals(): Promise<SavingsGoal[]> {
  return request<SavingsGoal[]>('/budgets/goals')
}

export async function createSavingsGoal(data: SavingsGoalInput): Promise<SavingsGoal> {
  return request<SavingsGoal>('/budgets/goals', {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export async function updateSavingsGoal(id: number, data: SavingsGoalUpdate): Promise<SavingsGoal> {
  return request<SavingsGoal>(`/budgets/goals/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  })
}

export async function deleteSavingsGoal(id: number): Promise<void> {
  await requestVoid(`/budgets/goals/${id}`, { method: 'DELETE' })
}

// ─── Assistant budget-creation (v9d) ──────────────────────────────────────────
// Creates budgets from natural language; also returns a normal chat `reply`.
// Throws on 503 when the AI provider is unconfigured.
export async function assistantBudget(messages: ChatMessage[]): Promise<BudgetChatResponse> {
  return request<BudgetChatResponse>('/assistant/budget', {
    method: 'POST',
    body: JSON.stringify({ messages }),
  })
}
