import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, X, Pencil, Trash2, PiggyBank, Target, Check, AlertTriangle } from 'lucide-react'
import {
  getSavingsGoals,
  createSavingsGoal,
  updateSavingsGoal,
  deleteSavingsGoal,
  getCategoryBudgets,
  createCategoryBudget,
  updateCategoryBudget,
  deleteCategoryBudget,
  getCategories,
} from '../lib/api'
import type {
  SavingsGoal,
  SavingsGoalInput,
  CategoryBudget,
  CategoryBudgetInput,
} from '../lib/types'
import DatePicker from './DatePicker'
import { iconForCategory } from '../lib/categoryIcons'
import { useAccountSelection } from '../lib/accountSelection'

// ─── Formatting ─────────────────────────────────────────────────────────────
function fmt(n: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(n)
}

function fmtMonthYear(iso: string | null): string {
  if (!iso) return 'No target date'
  const d = new Date(`${iso}T00:00:00`)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-US', { month: 'short', year: 'numeric' })
}

// Bar fill never exceeds the track; the `over` styling communicates the overage.
function clampPct(pct: number): number {
  if (!isFinite(pct) || pct < 0) return 0
  return Math.min(100, pct)
}

// ─── Confirm dialog ──────────────────────────────────────────────────────────
function ConfirmDialog({
  title,
  message,
  busy,
  onCancel,
  onConfirm,
}: {
  title: string
  message: string
  busy: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onCancel])

  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="modal" style={{ maxWidth: 420 }}>
        <div className="modal-header">
          <span className="modal-title">{title}</span>
          <button className="btn btn-ghost btn-icon" onClick={onCancel} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="modal-body">
          <p style={{ fontSize: 14, color: 'var(--ink-soft)', lineHeight: 1.6 }}>{message}</p>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn btn-secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn btn-danger" onClick={onConfirm} disabled={busy}>
            {busy ? <span className="spinner" style={{ width: 14, height: 14 }} /> : <Trash2 size={14} />}
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Savings goal modal ────────────────────────────────────────────────────────
function GoalModal({
  goal,
  accountLabels,
  onClose,
  onSaved,
}: {
  goal: SavingsGoal | null
  accountLabels: string[]
  onClose: () => void
  onSaved: () => void
}) {
  const isEdit = Boolean(goal)
  const [name, setName] = useState(goal?.name ?? '')
  const [target, setTarget] = useState(goal?.target_amount?.toString() ?? '')
  const [targetDate, setTargetDate] = useState(goal?.target_date ?? '')
  const [account, setAccount] = useState(goal?.account ?? '')
  const [saving, setSaving] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  function validate(): boolean {
    const errs: Record<string, string> = {}
    if (!name.trim()) errs.name = 'Name is required'
    const amt = parseFloat(target)
    if (!target || isNaN(amt) || amt <= 0) errs.target = 'Target must be a positive number'
    setErrors(errs)
    return Object.keys(errs).length === 0
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!validate()) return
    const data: SavingsGoalInput = {
      name: name.trim(),
      target_amount: parseFloat(target),
      target_date: targetDate || null,
      account: account.trim() || null,
    }
    setSaving(true)
    try {
      if (isEdit && goal) await updateSavingsGoal(goal.id, data)
      else await createSavingsGoal(data)
      onSaved()
    } catch (err) {
      setErrors({ submit: err instanceof Error ? err.message : 'Failed to save' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header">
          <span className="modal-title">{isEdit ? 'Edit Goal' : 'New Savings Goal'}</span>
          <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <form onSubmit={(e) => { void handleSubmit(e) }}>
          <div className="modal-body">
            {errors.submit && <div className="budget-form-error">{errors.submit}</div>}

            <div className="form-group">
              <label htmlFor="goal-name">Goal name</label>
              <input
                id="goal-name"
                type="text"
                placeholder="e.g. Japan Trip"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
              {errors.name && <div className="form-error">{errors.name}</div>}
            </div>

            <div className="form-row">
              <div className="form-group">
                <label htmlFor="goal-target">Target amount ($)</label>
                <input
                  id="goal-target"
                  type="number"
                  min="0.01"
                  step="0.01"
                  placeholder="2000"
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                />
                {errors.target && <div className="form-error">{errors.target}</div>}
              </div>
              <div className="form-group">
                <div className="label-row">
                  <label style={{ marginBottom: 0 }}>Target date (optional)</label>
                  {targetDate && (
                    <button type="button" className="ai-suggest-btn" onClick={() => setTargetDate('')}>
                      Clear
                    </button>
                  )}
                </div>
                <DatePicker
                  block
                  ariaLabel="Target date"
                  value={targetDate}
                  onChange={setTargetDate}
                  placeholder="No date"
                />
              </div>
            </div>

            <div className="form-group" style={{ marginBottom: 0 }}>
              <label htmlFor="goal-account">Savings account (optional)</label>
              <input
                id="goal-account"
                type="text"
                list="goal-account-options"
                placeholder="Which account holds these savings?"
                value={account}
                onChange={(e) => setAccount(e.target.value)}
                autoComplete="off"
              />
              <datalist id="goal-account-options">
                {accountLabels.map((a) => (
                  <option key={a} value={a} />
                ))}
              </datalist>
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? <span className="spinner" style={{ width: 14, height: 14 }} /> : null}
              {isEdit ? 'Save Changes' : 'Add Goal'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Category limit modal ──────────────────────────────────────────────────────
function LimitModal({
  limit,
  categoryOptions,
  onClose,
  onSaved,
}: {
  limit: CategoryBudget | null
  categoryOptions: string[]
  onClose: () => void
  onSaved: () => void
}) {
  const isEdit = Boolean(limit)
  const [category, setCategory] = useState(limit?.category ?? '')
  const [amount, setAmount] = useState(limit?.limit_amount?.toString() ?? '')
  const [saving, setSaving] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  function validate(): boolean {
    const errs: Record<string, string> = {}
    if (!category.trim()) errs.category = 'Category is required'
    const amt = parseFloat(amount)
    if (!amount || isNaN(amt) || amt <= 0) errs.amount = 'Limit must be a positive number'
    setErrors(errs)
    return Object.keys(errs).length === 0
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!validate()) return
    const data: CategoryBudgetInput = {
      category: category.trim(),
      limit_amount: parseFloat(amount),
    }
    setSaving(true)
    try {
      if (isEdit && limit) await updateCategoryBudget(limit.id, data)
      else await createCategoryBudget(data)
      onSaved()
    } catch (err) {
      setErrors({ submit: err instanceof Error ? err.message : 'Failed to save' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header">
          <span className="modal-title">{isEdit ? 'Edit Limit' : 'New Category Limit'}</span>
          <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <form onSubmit={(e) => { void handleSubmit(e) }}>
          <div className="modal-body">
            {errors.submit && <div className="budget-form-error">{errors.submit}</div>}

            <div className="form-group">
              <label htmlFor="limit-category">Category</label>
              <input
                id="limit-category"
                type="text"
                list="limit-category-options"
                placeholder="e.g. Food & Drink"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                autoComplete="off"
                disabled={isEdit}
                autoFocus={!isEdit}
              />
              <datalist id="limit-category-options">
                {categoryOptions.map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
              {errors.category && <div className="form-error">{errors.category}</div>}
            </div>

            <div className="form-group" style={{ marginBottom: 0 }}>
              <label htmlFor="limit-amount">Monthly limit ($)</label>
              <input
                id="limit-amount"
                type="number"
                min="0.01"
                step="0.01"
                placeholder="400"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
              {errors.amount && <div className="form-error">{errors.amount}</div>}
            </div>
          </div>
          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? <span className="spinner" style={{ width: 14, height: 14 }} /> : null}
              {isEdit ? 'Save Changes' : 'Add Limit'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Page ──────────────────────────────────────────────────────────────────────
type DeleteTarget =
  | { kind: 'goal'; goal: SavingsGoal }
  | { kind: 'limit'; limit: CategoryBudget }

export default function Budget() {
  const queryClient = useQueryClient()
  const { accounts } = useAccountSelection()
  const accountLabels = accounts.map((a) => a.label)

  const goalsQuery = useQuery({ queryKey: ['budgets', 'goals'], queryFn: getSavingsGoals })
  const limitsQuery = useQuery({ queryKey: ['budgets', 'categories'], queryFn: getCategoryBudgets })
  const categoriesQuery = useQuery({ queryKey: ['categories'], queryFn: getCategories })

  const goals = goalsQuery.data ?? []
  const limits = limitsQuery.data ?? []
  const categoryOptions = categoriesQuery.data ?? []
  const loading = goalsQuery.isPending || limitsQuery.isPending
  const loadError = goalsQuery.error ?? limitsQuery.error
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const error = deleteError ?? (loadError instanceof Error ? loadError.message : loadError ? 'Failed to load budget' : null)

  const invalidateBudgets = () => { void queryClient.invalidateQueries({ queryKey: ['budgets'] }) }
  const load = () => {
    void goalsQuery.refetch()
    void limitsQuery.refetch()
  }

  const [goalModal, setGoalModal] = useState<{ open: boolean; goal: SavingsGoal | null }>({ open: false, goal: null })
  const [limitModal, setLimitModal] = useState<{ open: boolean; limit: CategoryBudget | null }>({ open: false, limit: null })
  const [deleting, setDeleting] = useState<DeleteTarget | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)

  async function confirmDelete() {
    if (!deleting) return
    setDeleteBusy(true)
    setDeleteError(null)
    try {
      if (deleting.kind === 'goal') await deleteSavingsGoal(deleting.goal.id)
      else await deleteCategoryBudget(deleting.limit.id)
      setDeleting(null)
      invalidateBudgets()
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : 'Failed to delete')
    } finally {
      setDeleteBusy(false)
    }
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Planning · goals & limits</div>
        <h1 className="page-title">Budget</h1>
        <p className="page-subtitle">Set what you want to save and where you want to hold the line.</p>
      </div>

      {loading && (
        <div className="loading-state">
          <div className="spinner spinner-lg" />
          Loading budget…
        </div>
      )}

      {error && !loading && (
        <div className="error-state">
          <AlertTriangle size={32} />
          <div>{error}</div>
          <button className="btn btn-secondary btn-sm" onClick={load}>Retry</button>
        </div>
      )}

      {!loading && !error && (
        <>
          {/* ── Savings goals ─────────────────────────────────────────────── */}
          <section className="budget-section">
            <div className="budget-section-head">
              <div className="budget-section-title">
                <PiggyBank size={17} />
                <span>Savings goals</span>
              </div>
              <button className="btn btn-primary btn-sm" onClick={() => setGoalModal({ open: true, goal: null })}>
                <Plus size={14} /> Add goal
              </button>
            </div>

            {goals.length === 0 ? (
              <div className="budget-empty">No goals yet — ask the assistant or add one below.</div>
            ) : (
              <div className="goal-grid">
                {goals.map((g) => {
                  const trackState = g.on_track === true ? 'on' : 'off'
                  return (
                    <div key={g.id} className="goal-card">
                      <div className="goal-card-head">
                        <div className="goal-name">{g.name}</div>
                        <div className="goal-actions">
                          <button
                            className="btn btn-ghost btn-icon btn-sm"
                            aria-label="Edit goal"
                            onClick={() => setGoalModal({ open: true, goal: g })}
                          >
                            <Pencil size={14} />
                          </button>
                          <button
                            className="btn btn-ghost btn-icon btn-sm"
                            aria-label="Delete goal"
                            onClick={() => setDeleting({ kind: 'goal', goal: g })}
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </div>

                      <div className="goal-figures">
                        <span className="goal-saved">{fmt(g.saved)}</span>
                        <span className="goal-of">of {fmt(g.target_amount)}</span>
                      </div>

                      <div className="progress-track">
                        <div
                          className="progress-fill savings"
                          style={{ width: `${clampPct(g.pct)}%` }}
                        />
                      </div>

                      <div className="goal-meta">
                        <span>{fmt(g.remaining)} to go</span>
                        <span className={`goal-track-badge ${trackState}`}>
                          {g.on_track === true ? <Check size={11} /> : <AlertTriangle size={11} />}
                          {g.on_track === true ? 'On track' : g.on_track === false ? 'Behind' : 'No deadline'}
                        </span>
                      </div>

                      <div className="goal-footer">
                        <span>{fmtMonthYear(g.target_date)}</span>
                        {g.monthly_needed > 0 && (
                          <span className="goal-monthly">{fmt(g.monthly_needed)}/mo to stay on track</span>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </section>

          {/* ── Category limits ───────────────────────────────────────────── */}
          <section className="budget-section">
            <div className="budget-section-head">
              <div className="budget-section-title">
                <Target size={17} />
                <span>Category limits</span>
              </div>
              <button className="btn btn-primary btn-sm" onClick={() => setLimitModal({ open: true, limit: null })}>
                <Plus size={14} /> Add limit
              </button>
            </div>

            {limits.length === 0 ? (
              <div className="budget-empty">No limits yet — cap a category to keep spending in check.</div>
            ) : (
              <div className="limit-list">
                {limits.map((l) => {
                  const Icon = iconForCategory(l.category)
                  return (
                    <div key={l.id} className={`limit-row${l.over ? ' over' : ''}`}>
                      <div className="limit-head">
                        <span className="limit-icon"><Icon size={15} /></span>
                        <span className="limit-category">{l.category}</span>
                        <span className="limit-amount">
                          {fmt(l.spent)} <span className="limit-of">/ {fmt(l.limit_amount)}</span>
                        </span>
                        <div className="limit-actions">
                          <button
                            className="btn btn-ghost btn-icon btn-sm"
                            aria-label="Edit limit"
                            onClick={() => setLimitModal({ open: true, limit: l })}
                          >
                            <Pencil size={14} />
                          </button>
                          <button
                            className="btn btn-ghost btn-icon btn-sm"
                            aria-label="Delete limit"
                            onClick={() => setDeleting({ kind: 'limit', limit: l })}
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </div>

                      <div className="progress-track">
                        <div
                          className={`progress-fill${l.over ? ' over' : ''}`}
                          style={{ width: `${clampPct(l.pct)}%` }}
                        />
                      </div>

                      <div className="limit-meta">
                        <span>{Math.round(l.pct)}% used</span>
                        <span className={l.over ? 'neg' : ''}>
                          {l.over ? `${fmt(Math.abs(l.remaining))} over` : `${fmt(l.remaining)} left`}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </section>
        </>
      )}

      {goalModal.open && (
        <GoalModal
          goal={goalModal.goal}
          accountLabels={accountLabels}
          onClose={() => setGoalModal({ open: false, goal: null })}
          onSaved={() => { setGoalModal({ open: false, goal: null }); invalidateBudgets() }}
        />
      )}

      {limitModal.open && (
        <LimitModal
          limit={limitModal.limit}
          categoryOptions={categoryOptions}
          onClose={() => setLimitModal({ open: false, limit: null })}
          onSaved={() => { setLimitModal({ open: false, limit: null }); invalidateBudgets() }}
        />
      )}

      {deleting && (
        <ConfirmDialog
          title={deleting.kind === 'goal' ? 'Delete goal?' : 'Delete limit?'}
          message={
            deleting.kind === 'goal'
              ? `Remove the "${deleting.goal.name}" savings goal? This can't be undone.`
              : `Remove the ${deleting.limit.category} limit? This can't be undone.`
          }
          busy={deleteBusy}
          onCancel={() => setDeleting(null)}
          onConfirm={() => void confirmDelete()}
        />
      )}
    </div>
  )
}
