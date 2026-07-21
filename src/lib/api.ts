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
} from './types'

const BASE = '/api'

// A query value can be a scalar, or a string[] (serialized comma-joined, e.g. `accounts`).
type QueryValue = string | number | boolean | string[] | undefined
type QueryParams = Record<string, QueryValue>

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  return res.json() as Promise<T>
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
  await fetch(`${BASE}/transactions/${id}`, { method: 'DELETE' })
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
  const res = await fetch(`${BASE}/transactions/csv`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  return res.json() as Promise<CsvImportResult>
}

export function getCsvTemplateUrl(): string {
  return `${BASE}/transactions/csv/template`
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
  await fetch(`${BASE}/imports/${id}`, { method: 'DELETE' })
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

export async function createPlaidLinkToken(): Promise<{ link_token: string; expiration: string }> {
  return request<{ link_token: string; expiration: string }>('/plaid/link-token', {
    method: 'POST',
    body: JSON.stringify({}),
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
  const res = await fetch(`${BASE}/plaid/items/${id}`, { method: 'DELETE' })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
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
  await fetch(`${BASE}/rules/${id}`, { method: 'DELETE' })
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
  const res = await fetch(`${BASE}/budgets/categories/${id}`, { method: 'DELETE' })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
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
  const res = await fetch(`${BASE}/budgets/goals/${id}`, { method: 'DELETE' })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
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
