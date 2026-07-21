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
  dup_dismissed?: boolean // v7 — marked "not a duplicate", excluded from flagging
  source: TransactionSource
  created_at: string
  // ── Plaid enrichment (v9) ────────────────────────────────────────────────
  merchant_name?: string | null // cleaned merchant name; prefer for display
  logo_url?: string | null // merchant logo URL
  pending: boolean // still-pending charge (may change/disappear)
  pending_transaction_id?: string | null
  category_icon_url?: string | null // Plaid-provided category glyph URL
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
  accounts?: string[] // v9 — multi-account filter (comma-joined server-side)
  // v10 — hide these transaction types / categories server-side (comma-joined).
  // Accurate across pagination; empty/absent = no exclusion.
  exclude_types?: string[]
  exclude_categories?: string[]
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

// ─── Duplicate detection (v7) ──────────────────────────────────────────────────

export interface DuplicateGroup {
  group_key: string
  date: string // YYYY-MM-DD
  amount: number
  description: string | null
  account: string | null
  count: number
  total_extra: number // (count-1)*amount — the over-charged amount
  transactions: Transaction[]
}

export interface DismissDuplicatesResult {
  dismissed: number
}

// ─── Plaid bank sync (v8) ──────────────────────────────────────────────────────

export interface PlaidAccount {
  account_id: string
  name: string | null
  mask: string | null
  type: string | null
  subtype: string | null
  app_account: string | null
  // ── Balances (v9) ─────────────────────────────────────────────────────────
  available?: number | null
  current?: number | null
  currency?: string | null
}

export interface PlaidItem {
  id: number
  institution_name: string | null
  accounts: PlaidAccount[]
  last_synced_at: string | null
  status: string
  // ── Branding (v9) ──────────────────────────────────────────────────────────
  institution_logo?: string | null // base64 PNG (no data: prefix), may be null
  institution_color?: string | null // hex accent, may be null
}

// ── Investment holdings (v9) ──────────────────────────────────────────────────
export interface Holding {
  account?: string | null
  institution?: string | null
  security_name?: string | null
  ticker_symbol?: string | null
  quantity?: number | null
  price?: number | null
  value?: number | null
  currency?: string | null
}

export interface PlaidStatus {
  configured: boolean
  env: string
  products: string[]
  items: PlaidItem[]
}

export interface PlaidSyncResult {
  items_synced: number
  added: number
  modified: number
  removed: number
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

// ─── Budget: category limits & savings goals (v9d) ─────────────────────────────

export interface CategoryBudget {
  id: number
  category: string
  limit_amount: number
  period: string // e.g. "monthly"
  created_at: string
  // ── computed by the server ──────────────────────────────────────────────
  spent: number
  remaining: number
  pct: number // 0–100+ (may exceed 100 when over)
  over: boolean
}

export interface CategoryBudgetInput {
  category: string
  limit_amount: number
  period?: string
}

export type CategoryBudgetUpdate = Partial<CategoryBudgetInput>

export interface SavingsGoal {
  id: number
  name: string
  target_amount: number
  target_date: string | null // YYYY-MM-DD
  account: string | null // designated savings account label
  starting_balance: number
  created_at: string
  // ── computed by the server ──────────────────────────────────────────────
  current_balance: number
  saved: number
  pct: number // 0–100+
  remaining: number
  monthly_needed: number
  on_track: boolean | null // null when there's no target date to judge against
}

export interface SavingsGoalInput {
  name: string
  target_amount: number
  target_date?: string | null
  account?: string | null
}

export type SavingsGoalUpdate = Partial<SavingsGoalInput>

// Assistant budget-creation — creates budgets from natural language and returns
// a normal chat reply alongside whatever it created.
export interface BudgetChatResponse {
  reply: string
  created: {
    goals: SavingsGoal[]
    category_limits: CategoryBudget[]
  }
}
