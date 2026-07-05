import { useState, useEffect } from 'react'
import { X, Sparkles } from 'lucide-react'
import {
  createTransaction,
  updateTransaction,
  getCategories,
  getAccounts,
  getAssistantStatus,
  suggestCategory,
  createRule,
  previewRule,
  applyRules,
} from '../lib/api'
import type { Transaction, TransactionCreate, TransactionType, RuleCreate } from '../lib/types'

// Derive a sensible match token from a description (longest alphabetic word, e.g. "STARBUCKS").
function ruleTokenFrom(desc: string): string {
  const words = desc.toLowerCase().match(/[a-z][a-z'&-]{2,}/g) ?? []
  return words.reduce((longest, w) => (w.length > longest.length ? w : longest), desc.trim().toLowerCase())
}

interface Props {
  transaction?: Transaction | null
  onClose: () => void
  onSaved: () => void
}

const DEFAULT_CATEGORIES = [
  'Food & Dining',
  'Transportation',
  'Housing',
  'Entertainment',
  'Healthcare',
  'Shopping',
  'Utilities',
  'Education',
  'Salary',
  'Freelance',
  'Investment',
  'Other',
]

export default function TransactionModal({ transaction, onClose, onSaved }: Props) {
  const isEdit = Boolean(transaction)

  const [date, setDate] = useState(transaction?.date ?? new Date().toISOString().split('T')[0])
  const [amount, setAmount] = useState(transaction?.amount?.toString() ?? '')
  const [type, setType] = useState<TransactionType>(transaction?.type ?? 'expense')
  const [category, setCategory] = useState(transaction?.category ?? '')
  const [customCategory, setCustomCategory] = useState('')
  const [description, setDescription] = useState(transaction?.description ?? '')
  const [account, setAccount] = useState(transaction?.account ?? '')
  const [categories, setCategories] = useState<string[]>(DEFAULT_CATEGORIES)
  const [accounts, setAccounts] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const [aiEnabled, setAiEnabled] = useState(false)
  const [suggesting, setSuggesting] = useState(false)

  // "Learn a rule" from an edit (v5): when editing and category/type changes.
  const [learnRule, setLearnRule] = useState(false)
  const [ruleMatchCount, setRuleMatchCount] = useState<number | null>(null)
  const ruleToken = ruleTokenFrom(transaction?.description ?? '')

  useEffect(() => {
    getCategories()
      .then((cats) => {
        if (cats.length > 0) setCategories(cats)
      })
      .catch(() => { /* use defaults */ })
    getAccounts()
      .then(setAccounts)
      .catch(() => { /* no accounts yet */ })
    getAssistantStatus()
      .then((s) => setAiEnabled(s.enabled))
      .catch(() => setAiEnabled(false))
  }, [])

  async function handleAutoCategorize() {
    if (suggesting) return
    setSuggesting(true)
    setErrors((prev) => ({ ...prev, category: '' }))
    try {
      const { category: suggested } = await suggestCategory({
        description: description.trim(),
        amount: amount ? parseFloat(amount) : null,
        type,
      })
      if (categories.includes(suggested)) {
        setCategory(suggested)
      } else {
        setCategory('__custom__')
        setCustomCategory(suggested)
      }
    } catch (err) {
      setErrors((prev) => ({
        ...prev,
        category: err instanceof Error ? err.message : 'Could not suggest a category',
      }))
    } finally {
      setSuggesting(false)
    }
  }

  useEffect(() => {
    // Close on Escape
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  function validate(): boolean {
    const errs: Record<string, string> = {}
    if (!date) errs.date = 'Date is required'
    const amt = parseFloat(amount)
    if (!amount || isNaN(amt) || amt <= 0) errs.amount = 'Amount must be a positive number'
    const finalCategory = category === '__custom__' ? customCategory : category
    if (!finalCategory.trim()) errs.category = 'Category is required'
    setErrors(errs)
    return Object.keys(errs).length === 0
  }

  const effectiveCategory = category === '__custom__' ? customCategory.trim() : category
  // Show the "learn a rule" affordance only on edit, when type/category changed and we have a token.
  const categoryChanged = isEdit && transaction != null && effectiveCategory !== transaction.category && !!effectiveCategory
  const typeChanged = isEdit && transaction != null && type !== transaction.type
  const canLearnRule = (categoryChanged || typeChanged) && !!ruleToken

  function buildLearnedRule(): RuleCreate {
    return {
      name: `${ruleToken} → ${categoryChanged ? effectiveCategory : type}`,
      match_field: 'description',
      match_op: 'contains',
      match_value: ruleToken,
      set_category: categoryChanged ? effectiveCategory : null,
      set_type: typeChanged ? type : null,
    }
  }

  // Preview how many existing rows a learned rule would touch.
  useEffect(() => {
    if (!learnRule || !canLearnRule) { setRuleMatchCount(null); return }
    let cancelled = false
    previewRule(buildLearnedRule())
      .then((r) => { if (!cancelled) setRuleMatchCount(r.matches) })
      .catch(() => { if (!cancelled) setRuleMatchCount(null) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [learnRule, canLearnRule, ruleToken, effectiveCategory, type])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!validate()) return

    const finalCategory = category === '__custom__' ? customCategory.trim() : category

    const data: TransactionCreate = {
      date,
      amount: parseFloat(amount),
      type,
      category: finalCategory,
      description: description.trim() || null,
      account: account.trim() || null,
    }

    setSaving(true)
    try {
      if (isEdit && transaction) {
        await updateTransaction(transaction.id, data)
        // Learn-a-rule: persist a rule and re-apply to existing matches.
        if (learnRule && canLearnRule) {
          try {
            await createRule(buildLearnedRule())
            await applyRules({})
          } catch { /* rule creation is best-effort; the edit already saved */ }
        }
      } else {
        await createTransaction(data)
      }
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
          <span className="modal-title">{isEdit ? 'Edit Transaction' : 'New Transaction'}</span>
          <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <form onSubmit={(e) => { void handleSubmit(e) }}>
          <div className="modal-body">
            {errors.submit && (
              <div style={{
                background: 'var(--danger-dim)',
                border: '1px solid rgba(244,63,94,0.3)',
                borderRadius: 'var(--radius-sm)',
                padding: '10px 14px',
                color: 'var(--danger)',
                fontSize: 13,
                marginBottom: 16,
              }}>
                {errors.submit}
              </div>
            )}

            <div className="form-row">
              <div className="form-group">
                <label htmlFor="tx-date">Date</label>
                <input
                  id="tx-date"
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                />
                {errors.date && <div className="form-error">{errors.date}</div>}
              </div>

              <div className="form-group">
                <label htmlFor="tx-amount">Amount ($)</label>
                <input
                  id="tx-amount"
                  type="number"
                  min="0.01"
                  step="0.01"
                  placeholder="0.00"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                />
                {errors.amount && <div className="form-error">{errors.amount}</div>}
              </div>
            </div>

            <div className="form-group">
              <label>Type</label>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {(['income', 'expense', 'transfer', 'refund'] as const).map((t) => {
                  const activeColor =
                    t === 'income' ? 'var(--success)'
                    : t === 'expense' ? 'var(--danger)'
                    : t === 'refund' ? 'var(--success)'
                    : 'var(--text-secondary)'
                  const activeBg =
                    t === 'income' ? 'var(--success-dim)'
                    : t === 'expense' ? 'var(--danger-dim)'
                    : t === 'refund' ? 'var(--success-dim)'
                    : 'var(--bg-elevated)'
                  return (
                    <button
                      key={t}
                      type="button"
                      onClick={() => setType(t)}
                      style={{
                        flex: '1 1 22%',
                        minWidth: 72,
                        padding: '9px',
                        borderRadius: 'var(--radius-sm)',
                        border: `1px solid ${type === t ? activeColor : 'var(--border)'}`,
                        background: type === t ? activeBg : 'var(--bg-elevated)',
                        color: type === t ? activeColor : 'var(--text-muted)',
                        cursor: 'pointer',
                        fontWeight: 600,
                        fontSize: 14,
                        fontFamily: 'inherit',
                        transition: 'all var(--transition)',
                        textTransform: 'capitalize',
                      }}
                    >
                      {t}
                    </button>
                  )
                })}
              </div>
              {type === 'transfer' && (
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 8 }}>
                  Transfers move money between your own accounts and are excluded from spending stats.
                </div>
              )}
              {type === 'refund' && (
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 8 }}>
                  A refund reduces spending in its category (nets against expenses) — not income.
                </div>
              )}
            </div>

            <div className="form-group">
              <div className="label-row">
                <label htmlFor="tx-category" style={{ marginBottom: 0 }}>Category</label>
                {aiEnabled && (
                  <button
                    type="button"
                    className="ai-suggest-btn"
                    onClick={() => void handleAutoCategorize()}
                    disabled={suggesting || !description.trim()}
                    title={description.trim() ? 'Suggest a category from the description' : 'Add a description first'}
                  >
                    {suggesting ? (
                      <span className="spinner" style={{ width: 12, height: 12 }} />
                    ) : (
                      <Sparkles size={13} />
                    )}
                    {suggesting ? 'Thinking…' : 'Auto'}
                  </button>
                )}
              </div>
              <select
                id="tx-category"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                <option value="">Select category…</option>
                {categories.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
                <option value="__custom__">+ Custom category</option>
              </select>
              {errors.category && <div className="form-error">{errors.category}</div>}
            </div>

            {category === '__custom__' && (
              <div className="form-group">
                <label htmlFor="tx-custom-cat">Custom Category</label>
                <input
                  id="tx-custom-cat"
                  type="text"
                  placeholder="Enter category name"
                  value={customCategory}
                  onChange={(e) => setCustomCategory(e.target.value)}
                  autoFocus
                />
              </div>
            )}

            <div className="form-group">
              <label htmlFor="tx-account">Account / Card (optional)</label>
              <input
                id="tx-account"
                type="text"
                list="tx-account-options"
                placeholder="Unassigned — type or pick a card"
                value={account}
                onChange={(e) => setAccount(e.target.value)}
                autoComplete="off"
              />
              <datalist id="tx-account-options">
                {accounts.map((a) => (
                  <option key={a} value={a} />
                ))}
              </datalist>
            </div>

            <div className="form-group" style={{ marginBottom: canLearnRule ? 16 : 0 }}>
              <label htmlFor="tx-desc">Description (optional)</label>
              <textarea
                id="tx-desc"
                rows={2}
                placeholder="Add a note…"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                style={{ resize: 'vertical' }}
              />
            </div>

            {canLearnRule && (
              <div className="learn-rule-box">
                <label className="rule-enable-row">
                  <input type="checkbox" checked={learnRule} onChange={(e) => setLearnRule(e.target.checked)} />
                  <span>
                    Always apply to transactions matching <strong>"{ruleToken}"</strong>
                    {learnRule && ruleMatchCount != null && (
                      <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
                        {' '}(will also update {ruleMatchCount} existing)
                      </span>
                    )}
                  </span>
                </label>
                {learnRule && (
                  <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 6 }}>
                    Creates a rule: description contains "{ruleToken}" →{' '}
                    {categoryChanged && <>category <strong>{effectiveCategory}</strong></>}
                    {categoryChanged && typeChanged && ', '}
                    {typeChanged && <>type <strong>{type}</strong></>}.
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? (
                <>
                  <span className="spinner" style={{ width: 14, height: 14 }} />
                  Saving…
                </>
              ) : isEdit ? 'Save Changes' : 'Add Transaction'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
