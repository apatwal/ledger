// ─── Transaction ─────────────────────────────────────────────────────────────

export type TransactionType = 'income' | 'expense' | 'transfer' | 'refund'
export type TransactionSource = 'manual' | 'csv'

export interface Transaction {
  id: number
  date: string // YYYY-MM-DD
  amount: number // float > 0
  type: TransactionType
  category: string
  description: string | null
  account: string | null // which card/account; null = Unassigned (v4)
  needs_review: boolean // v5
  review_reason: string | null // v5
  source: TransactionSource
  created_at: string
}

export interface TransactionCreate {
  date: string
  amount: number
  type: TransactionType
  category: string
  description?: string | null
  account?: string | null
}

export interface TransactionUpdate {
  date?: string
  amount?: number
  type?: TransactionType
  category?: string
  description?: string | null
  account?: string | null
}

// ─── Query params ─────────────────────────────────────────────────────────────

export interface TransactionQuery {
  start_date?: string
  end_date?: string
  type?: TransactionType
  category?: string
  account?: string
  needs_review?: boolean
  limit?: number
  offset?: number
}

// ─── Stats ───────────────────────────────────────────────────────────────────

export interface StatsSummary {
  total_income: number
  total_expense: number
  net: number
  savings: number
  savings_rate: number
  count: number
}

export interface StatsByCategory {
  category: string
  total: number
  count: number
  pct: number
}

export type Granularity = 'day' | 'week' | 'month' | 'year'

export interface StatsOverTime {
  period: string
  income: number
  expense: number
  net: number
  savings: number
}

export interface AccountStat {
  account: string
  income: number
  expense: number
  net: number
  count: number
}

// ─── CSV ─────────────────────────────────────────────────────────────────────

export interface CsvImportError {
  row: number
  reason: string
}

export interface CsvImportResult {
  imported: number
  skipped: number
  transfers: number
  needs_review: number // v5
  batch_id: number // v5.2
  errors: CsvImportError[]
}

// ─── Import history (v5.2) ─────────────────────────────────────────────────────

export type StatementType = 'card' | 'bank'

export interface ImportBatch {
  id: number
  filename: string
  account: string | null
  statement_type?: StatementType // v5.3 (optional in history)
  imported: number
  skipped: number
  transfers: number
  needs_review: number
  created_at: string
}

export interface ReassignImportResult {
  updated: number
}

// ─── Rules engine (v5) ─────────────────────────────────────────────────────────

export type RuleMatchField = 'description' | 'category' | 'account' | 'any'
export type RuleMatchOp = 'contains' | 'equals' | 'regex'

export interface Rule {
  id: number
  name: string | null
  priority: number
  enabled: boolean
  match_field: RuleMatchField
  match_op: RuleMatchOp
  match_value: string
  amount_min: number | null
  amount_max: number | null
  set_type: TransactionType | null
  set_category: string | null
  set_account: string | null
  created_at: string
}

export interface RuleCreate {
  name?: string | null
  priority?: number
  enabled?: boolean
  match_field: RuleMatchField
  match_op: RuleMatchOp
  match_value: string
  amount_min?: number | null
  amount_max?: number | null
  set_type?: TransactionType | null
  set_category?: string | null
  set_account?: string | null
}

export type RuleUpdate = Partial<RuleCreate>

export interface ApplyRulesResult {
  updated: number
}

export interface PreviewRuleResult {
  matches: number
}

export interface CategorizeBatchItem {
  id: number
  category: string
  confidence: number
}

export interface CategorizeBatchResult {
  results: CategorizeBatchItem[]
}

// ─── Health ──────────────────────────────────────────────────────────────────

export interface HealthResponse {
  status: string
}

// ─── AI assistant ────────────────────────────────────────────────────────────

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface AssistantReply {
  reply: string
  tool_calls: string[]
}

export interface CategorySuggestion {
  category: string
  confidence: number
}

export interface InsightsResult {
  insights: string
}

export interface AssistantStatus {
  enabled: boolean
}
