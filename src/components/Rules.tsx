import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, X, Wand2, AlertCircle, Check } from 'lucide-react'
import {
  getRules,
  createRule,
  updateRule,
  deleteRule,
  applyRules,
  previewRule,
  getCategories,
  getAccounts,
} from '../lib/api'
import type { Rule, RuleCreate, RuleMatchField, RuleMatchOp, TransactionType } from '../lib/types'

const MATCH_FIELDS: RuleMatchField[] = ['description', 'category', 'account', 'any']
const MATCH_OPS: RuleMatchOp[] = ['contains', 'equals', 'regex']

function actionsSummary(r: Rule): string {
  const parts: string[] = []
  if (r.set_type) parts.push(`type → ${r.set_type}`)
  if (r.set_category) parts.push(`category → ${r.set_category}`)
  if (r.set_account) parts.push(`account → ${r.set_account}`)
  return parts.length ? parts.join(', ') : '—'
}

export default function Rules() {
  const queryClient = useQueryClient()
  const rulesQuery = useQuery({ queryKey: ['rules'], queryFn: () => getRules() })
  const rules = rulesQuery.data ?? []
  const loading = rulesQuery.isPending
  const [actionError, setActionError] = useState<string | null>(null)
  const error = actionError ?? (rulesQuery.error instanceof Error ? rulesQuery.error.message : rulesQuery.error ? 'Failed to load rules' : null)

  const invalidateRules = () => { void queryClient.invalidateQueries({ queryKey: ['rules'] }) }

  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Rule | null>(null)

  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  const [applying, setApplying] = useState(false)
  const [applyMsg, setApplyMsg] = useState<string | null>(null)

  async function handleToggle(rule: Rule) {
    setBusyId(rule.id)
    setActionError(null)
    try {
      await updateRule(rule.id, { enabled: !rule.enabled })
      invalidateRules()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Failed to update rule')
    } finally {
      setBusyId(null)
    }
  }

  async function handleDelete(id: number) {
    setBusyId(id)
    setActionError(null)
    try {
      await deleteRule(id)
      setDeleteConfirmId(null)
      invalidateRules()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setBusyId(null)
    }
  }

  async function handleReapply() {
    setApplying(true)
    setApplyMsg(null)
    try {
      const { updated } = await applyRules({})
      setApplyMsg(`Re-applied rules — ${updated} transaction${updated === 1 ? '' : 's'} updated.`)
      // Applying rules rewrites transactions → their categories/types and every
      // derived stat may have changed.
      void queryClient.invalidateQueries({ queryKey: ['transactions'] })
      void queryClient.invalidateQueries({ queryKey: ['stats'] })
      void queryClient.invalidateQueries({ queryKey: ['duplicates'] })
    } catch (e) {
      setApplyMsg(e instanceof Error ? e.message : 'Failed to apply rules')
    } finally {
      setApplying(false)
    }
  }

  return (
    <div>
      <div className="page-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <h1 className="page-title">Rules</h1>
          <p className="page-subtitle">Auto-classify transactions as they're imported. First matching rule wins.</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-secondary" onClick={() => void handleReapply()} disabled={applying}>
            {applying ? <span className="spinner" style={{ width: 14, height: 14 }} /> : <Wand2 size={15} />}
            Re-apply to existing
          </button>
          <button className="btn btn-primary" onClick={() => { setEditing(null); setModalOpen(true) }}>
            <Plus size={16} />
            New rule
          </button>
        </div>
      </div>

      {applyMsg && (
        <div className="card card-sm" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Check size={16} style={{ color: 'var(--green)' }} />
          {applyMsg}
        </div>
      )}

      {error && (
        <div className="error-state" style={{ padding: 24, marginBottom: 16 }}>
          <AlertCircle size={24} />
          {error}
        </div>
      )}

      <div className="table-wrapper">
        {loading ? (
          <div className="loading-state">
            <div className="spinner spinner-lg" />
            Loading rules…
          </div>
        ) : rules.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">
              <Wand2 size={40} />
            </div>
            <div>No rules yet. Create one to auto-classify messy statements.</div>
            <button className="btn btn-primary btn-sm" onClick={() => { setEditing(null); setModalOpen(true) }}>
              Create your first rule
            </button>
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th style={{ width: 70 }}>Priority</th>
                <th>Rule</th>
                <th>Match</th>
                <th>Sets</th>
                <th style={{ textAlign: 'center', width: 90 }}>Enabled</th>
                <th style={{ textAlign: 'center', width: 110 }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((r) => (
                <tr key={r.id} style={{ opacity: r.enabled ? 1 : 0.55 }}>
                  <td className="mono" style={{ color: 'var(--text-secondary)' }}>{r.priority}</td>
                  <td style={{ fontWeight: 500 }}>{r.name || <span style={{ color: 'var(--text-muted)' }}>Untitled</span>}</td>
                  <td style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
                    <span className="mono">{r.match_field}</span> {r.match_op}{' '}
                    <strong style={{ color: 'var(--text-primary)' }}>"{r.match_value}"</strong>
                    {(r.amount_min != null || r.amount_max != null) && (
                      <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                        {' '}· amt {r.amount_min ?? '–'}…{r.amount_max ?? '–'}
                      </span>
                    )}
                  </td>
                  <td style={{ color: 'var(--text-secondary)', fontSize: 13 }}>{actionsSummary(r)}</td>
                  <td style={{ textAlign: 'center' }}>
                    <button
                      className={`rule-toggle${r.enabled ? ' on' : ''}`}
                      onClick={() => void handleToggle(r)}
                      disabled={busyId === r.id}
                      role="switch"
                      aria-checked={r.enabled}
                      aria-label={r.enabled ? 'Disable rule' : 'Enable rule'}
                    >
                      <span className="rule-toggle-knob" />
                    </button>
                  </td>
                  <td>
                    <div style={{ display: 'flex', gap: 6, justifyContent: 'center' }}>
                      <button className="btn btn-ghost btn-icon btn-sm" onClick={() => { setEditing(r); setModalOpen(true) }} title="Edit">
                        <Pencil size={14} />
                      </button>
                      {deleteConfirmId === r.id ? (
                        <>
                          <button className="btn btn-danger btn-sm" onClick={() => void handleDelete(r.id)} disabled={busyId === r.id}>
                            {busyId === r.id ? <span className="spinner" style={{ width: 12, height: 12 }} /> : 'Confirm'}
                          </button>
                          <button className="btn btn-ghost btn-sm" onClick={() => setDeleteConfirmId(null)}>Cancel</button>
                        </>
                      ) : (
                        <button className="btn btn-ghost btn-icon btn-sm" onClick={() => setDeleteConfirmId(r.id)} title="Delete" style={{ color: 'var(--danger)' }}>
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
      </div>

      {modalOpen && (
        <RuleModal
          rule={editing}
          onClose={() => setModalOpen(false)}
          onSaved={() => { setModalOpen(false); invalidateRules() }}
        />
      )}
    </div>
  )
}

// ─── Rule create/edit modal ──────────────────────────────────────────────────

function RuleModal({ rule, onClose, onSaved }: { rule: Rule | null; onClose: () => void; onSaved: () => void }) {
  const isEdit = Boolean(rule)

  const [name, setName] = useState(rule?.name ?? '')
  const [priority, setPriority] = useState(String(rule?.priority ?? 100))
  const [enabled, setEnabled] = useState(rule?.enabled ?? true)
  const [matchField, setMatchField] = useState<RuleMatchField>(rule?.match_field ?? 'description')
  const [matchOp, setMatchOp] = useState<RuleMatchOp>(rule?.match_op ?? 'contains')
  const [matchValue, setMatchValue] = useState(rule?.match_value ?? '')
  const [amountMin, setAmountMin] = useState(rule?.amount_min != null ? String(rule.amount_min) : '')
  const [amountMax, setAmountMax] = useState(rule?.amount_max != null ? String(rule.amount_max) : '')
  const [setType, setSetType] = useState<'' | TransactionType>(rule?.set_type ?? '')
  const [setCategory, setSetCategory] = useState(rule?.set_category ?? '')
  const [setAccount, setSetAccount] = useState(rule?.set_account ?? '')

  const { data: categories = [] } = useQuery({ queryKey: ['categories'], queryFn: getCategories })
  const { data: accounts = [] } = useQuery({ queryKey: ['accounts'], queryFn: getAccounts })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [matchCount, setMatchCount] = useState<number | null>(null)

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  function buildPayload(): RuleCreate {
    return {
      name: name.trim() || null,
      priority: parseInt(priority, 10) || 100,
      enabled,
      match_field: matchField,
      match_op: matchOp,
      match_value: matchValue.trim(),
      amount_min: amountMin.trim() ? parseFloat(amountMin) : null,
      amount_max: amountMax.trim() ? parseFloat(amountMax) : null,
      set_type: setType || null,
      set_category: setCategory.trim() || null,
      set_account: setAccount.trim() || null,
    }
  }

  async function handlePreview() {
    if (!matchValue.trim()) { setMatchCount(null); return }
    try {
      const { matches } = await previewRule(buildPayload())
      setMatchCount(matches)
    } catch {
      setMatchCount(null)
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!matchValue.trim()) { setErr('Match value is required'); return }
    if (!setType && !setCategory.trim() && !setAccount.trim()) {
      setErr('Set at least one action (type, category, or account)')
      return
    }
    setSaving(true)
    setErr(null)
    try {
      if (isEdit && rule) await updateRule(rule.id, buildPayload())
      else await createRule(buildPayload())
      onSaved()
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : 'Failed to save rule')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header">
          <span className="modal-title">{isEdit ? 'Edit Rule' : 'New Rule'}</span>
          <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </div>

        <form onSubmit={(e) => { void handleSubmit(e) }}>
          <div className="modal-body">
            {err && (
              <div style={{ background: 'var(--danger-dim)', border: '1px solid rgba(168,50,46,0.3)', borderRadius: 'var(--radius-sm)', padding: '10px 14px', color: 'var(--danger)', fontSize: 13, marginBottom: 16 }}>
                {err}
              </div>
            )}

            <div className="form-row">
              <div className="form-group">
                <label htmlFor="r-name">Name (optional)</label>
                <input id="r-name" type="text" placeholder="e.g. Starbucks → Food & Drink" value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="form-group">
                <label htmlFor="r-priority">Priority</label>
                <input id="r-priority" type="number" value={priority} onChange={(e) => setPriority(e.target.value)} />
              </div>
            </div>

            <div className="form-group">
              <label>Match</label>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
                <select value={matchField} onChange={(e) => setMatchField(e.target.value as RuleMatchField)} aria-label="Match field">
                  {MATCH_FIELDS.map((f) => <option key={f} value={f}>{f}</option>)}
                </select>
                <select value={matchOp} onChange={(e) => setMatchOp(e.target.value as RuleMatchOp)} aria-label="Match operator">
                  {MATCH_OPS.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
              </div>
              <input
                type="text"
                placeholder="Match value (e.g. starbucks)"
                value={matchValue}
                onChange={(e) => { setMatchValue(e.target.value); setMatchCount(null) }}
                onBlur={() => void handlePreview()}
              />
              {matchCount != null && (
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 6 }}>
                  Matches <strong style={{ color: 'var(--text-secondary)' }}>{matchCount}</strong> existing transaction{matchCount === 1 ? '' : 's'}.
                </div>
              )}
            </div>

            <div className="form-row">
              <div className="form-group">
                <label htmlFor="r-amin">Amount min (optional)</label>
                <input id="r-amin" type="number" step="0.01" placeholder="—" value={amountMin} onChange={(e) => setAmountMin(e.target.value)} />
              </div>
              <div className="form-group">
                <label htmlFor="r-amax">Amount max (optional)</label>
                <input id="r-amax" type="number" step="0.01" placeholder="—" value={amountMax} onChange={(e) => setAmountMax(e.target.value)} />
              </div>
            </div>

            <div className="form-group">
              <label>Actions — set when matched (at least one)</label>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
                <select value={setType} onChange={(e) => setSetType(e.target.value as '' | TransactionType)} aria-label="Set type">
                  <option value="">Type: keep</option>
                  <option value="income">income</option>
                  <option value="expense">expense</option>
                  <option value="transfer">transfer</option>
                </select>
                <input type="text" list="r-cats" placeholder="Category" value={setCategory} onChange={(e) => setSetCategory(e.target.value)} autoComplete="off" />
                <input type="text" list="r-accts" placeholder="Account" value={setAccount} onChange={(e) => setSetAccount(e.target.value)} autoComplete="off" />
                <datalist id="r-cats">{categories.map((c) => <option key={c} value={c} />)}</datalist>
                <datalist id="r-accts">{accounts.map((a) => <option key={a} value={a} />)}</datalist>
              </div>
            </div>

            <label className="rule-enable-row">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              Enabled
            </label>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? <><span className="spinner" style={{ width: 14, height: 14 }} /> Saving…</> : isEdit ? 'Save Changes' : 'Create Rule'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
