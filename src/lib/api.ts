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
} from './types'

const BASE = '/api'

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

function toQuery(params: Record<string, string | number | boolean | undefined>): string {
  const q = new URLSearchParams()
  for (const [key, val] of Object.entries(params)) {
    if (val !== undefined && val !== null && val !== '') {
      q.set(key, String(val))
    }
  }
  const s = q.toString()
  return s ? `?${s}` : ''
}

// ─── Transactions ─────────────────────────────────────────────────────────────

export async function getTransactions(query?: TransactionQuery): Promise<Transaction[]> {
  const qs = toQuery(query as Record<string, string | number | boolean | undefined> ?? {})
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

// ─── Stats ────────────────────────────────────────────────────────────────────

export interface DateRangeParams {
  start_date?: string
  end_date?: string
  account?: string
}

export async function getStatsSummary(params?: DateRangeParams): Promise<StatsSummary> {
  const qs = toQuery(params as Record<string, string | number | boolean | undefined> ?? {})
  return request<StatsSummary>(`/stats/summary${qs}`)
}

export async function getStatsByCategory(
  params?: DateRangeParams & { type?: 'income' | 'expense' }
): Promise<StatsByCategory[]> {
  const qs = toQuery(params as Record<string, string | number | boolean | undefined> ?? {})
  return request<StatsByCategory[]>(`/stats/by-category${qs}`)
}

export async function getStatsOverTime(
  granularity: Granularity,
  params?: DateRangeParams
): Promise<StatsOverTime[]> {
  const qs = toQuery({
    granularity,
    ...(params as Record<string, string | number | boolean | undefined> ?? {}),
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
  const qs = toQuery(params as Record<string, string | number | boolean | undefined> ?? {})
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
