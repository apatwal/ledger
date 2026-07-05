import { useState, useRef, useCallback, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { Upload, Download, FileText, CheckCircle, AlertTriangle, XCircle, X, ArrowLeftRight, CreditCard, Eye, Sparkles, History, Trash2, Check, Landmark } from 'lucide-react'
import { importCsv, getCsvTemplateUrl, getAccounts, getAssistantStatus, categorizeBatch, getImports, reassignImport, deleteImport } from '../lib/api'
import type { CsvImportResult, ImportBatch, StatementType } from '../lib/types'

// Lightweight client-side peek: does the file look like a bank/checking export
// (a header row with a running-balance / balance column)? If so, pre-select "bank".
async function detectBankStatement(f: File): Promise<boolean> {
  try {
    const text = await f.slice(0, 8192).text()
    const lines = text.split(/\r?\n/).slice(0, 15)
    return lines.some((line) => {
      const norm = line.toLowerCase()
      return /(^|,)\s*"?(running bal|running balance|balance|available balance)"?\s*(,|$)/.test(norm)
    })
  } catch {
    return false
  }
}

export default function CsvImport() {
  const [dragOver, setDragOver] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [account, setAccount] = useState('')
  const [accounts, setAccounts] = useState<string[]>([])
  const [statementType, setStatementType] = useState<StatementType>('card')
  const [autoDetectedBank, setAutoDetectedBank] = useState(false)
  const [importing, setImporting] = useState(false)
  const [result, setResult] = useState<CsvImportResult | null>(null)
  const [importedAccount, setImportedAccount] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [aiEnabled, setAiEnabled] = useState(false)
  const [categorizing, setCategorizing] = useState(false)
  const [aiMsg, setAiMsg] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Import history (v5.2)
  const [imports, setImports] = useState<ImportBatch[]>([])
  const [batchBusyId, setBatchBusyId] = useState<number | null>(null)
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null)
  const [batchMsg, setBatchMsg] = useState<string | null>(null)

  const loadImports = useCallback(() => {
    getImports()
      .then(setImports)
      .catch(() => { /* ignore */ })
  }, [])

  function refreshAccounts() {
    getAccounts()
      .then(setAccounts)
      .catch(() => { /* no accounts yet */ })
  }

  useEffect(() => {
    refreshAccounts()
    loadImports()
    getAssistantStatus()
      .then((s) => setAiEnabled(s.enabled))
      .catch(() => setAiEnabled(false))
  }, [loadImports])

  async function handleReassign(batch: ImportBatch, account: string) {
    setBatchBusyId(batch.id)
    setBatchMsg(null)
    try {
      const { updated } = await reassignImport(batch.id, account || null)
      setBatchMsg(`Reassigned ${updated} transaction${updated === 1 ? '' : 's'} to ${account.trim() || 'Unassigned'}.`)
      loadImports()
      refreshAccounts()
    } catch (e) {
      setBatchMsg(e instanceof Error ? e.message : 'Reassign failed')
    } finally {
      setBatchBusyId(null)
    }
  }

  async function handleDeleteImport(id: number) {
    setBatchBusyId(id)
    setBatchMsg(null)
    try {
      await deleteImport(id)
      setDeleteConfirmId(null)
      setBatchMsg('Import undone — its transactions were removed.')
      loadImports()
      refreshAccounts()
    } catch (e) {
      setBatchMsg(e instanceof Error ? e.message : 'Delete failed')
    } finally {
      setBatchBusyId(null)
    }
  }

  async function handleAutoCategorize() {
    setCategorizing(true)
    setAiMsg(null)
    try {
      const { results } = await categorizeBatch({
        only_uncategorized: true,
        ...(importedAccount ? { account: importedAccount } : {}),
      })
      setAiMsg(`AI categorized ${results.length} transaction${results.length === 1 ? '' : 's'}.`)
    } catch (e) {
      setAiMsg(e instanceof Error ? e.message : 'Auto-categorize failed')
    } finally {
      setCategorizing(false)
    }
  }

  const handleFile = useCallback((f: File) => {
    if (!f.name.toLowerCase().endsWith('.csv')) {
      setError('Please select a CSV file (.csv)')
      return
    }
    setFile(f)
    setResult(null)
    setError(null)
    // Peek at the file: if it has a running-balance/balance column, pre-select "bank".
    void detectBankStatement(f).then((isBank) => {
      setAutoDetectedBank(isBank)
      setStatementType(isBank ? 'bank' : 'card')
    })
  }, [])

  function onDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }

  function onInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (f) handleFile(f)
  }

  async function handleImport() {
    if (!file) return
    setImporting(true)
    setError(null)
    try {
      const res = await importCsv(file, account, statementType)
      setResult(res)
      setImportedAccount(account.trim())
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
      loadImports()
      refreshAccounts()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Import failed')
    } finally {
      setImporting(false)
    }
  }

  function clearResult() {
    setResult(null)
    setError(null)
    setFile(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  return (
    <div>
      <div className="page-header" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <div className="page-eyebrow">Bulk entry</div>
          <h1 className="page-title">Import from CSV</h1>
          <p className="page-subtitle">Post many entries to the ledger at once.</p>
        </div>
        <a
          href={getCsvTemplateUrl()}
          download
          className="btn btn-secondary"
          style={{ textDecoration: 'none' }}
        >
          <Download size={15} />
          Download template
        </a>
      </div>

      {/* Instructions */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
          <FileText size={20} style={{ color: 'var(--accent-hover)', flexShrink: 0, marginTop: 2 }} />
          <div>
            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 6 }}>Works with most bank exports</div>
            <div style={{ fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.7 }}>
              Drop in a CSV exported from most banks (Discover, Chase, Capital One, Citi, Amex, etc.) —
              we auto-detect the columns. You don't need to rename headers. Our standard columns are:
              <code style={{
                display: 'inline-block',
                marginTop: 8,
                padding: '6px 12px',
                background: 'var(--bg-elevated)',
                borderRadius: 'var(--radius-xs)',
                fontFamily: 'monospace',
                fontSize: 13,
                color: 'var(--text-primary)',
                border: '1px solid var(--border)',
              }}>
                date, amount, type, category, description
              </code>
              <br />
              <span style={{ marginTop: 6, display: 'block' }}>
                <strong style={{ color: 'var(--text-secondary)' }}>date</strong>: YYYY-MM-DD or MM/DD/YYYY &nbsp;|&nbsp;
                <strong style={{ color: 'var(--text-secondary)' }}>amount</strong>: positive number, or separate debit/credit columns &nbsp;|&nbsp;
                <strong style={{ color: 'var(--text-secondary)' }}>description</strong>: optional
              </span>
              <span style={{ marginTop: 6, display: 'block' }}>
                Credit-card payments and transfers between your own accounts are auto-detected and excluded
                from spending so purchases aren't double-counted.
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Account / card selector */}
      <div className="card card-sm" style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <CreditCard size={18} style={{ color: 'var(--accent-hover)', flexShrink: 0 }} />
          <div style={{ flex: 1, minWidth: 200 }}>
            <label htmlFor="csv-account" style={{ marginBottom: 4 }}>Importing into</label>
            <input
              id="csv-account"
              type="text"
              list="csv-account-options"
              placeholder="Which card is this file from? e.g. Chase, Discover"
              value={account}
              onChange={(e) => setAccount(e.target.value)}
              autoComplete="off"
            />
            <datalist id="csv-account-options">
              {accounts.map((a) => (
                <option key={a} value={a} />
              ))}
            </datalist>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.5 }}>
              A CSV doesn't say which card it's from. Every row in this file will be tagged with this
              account. Leave blank to import as <strong style={{ color: 'var(--text-secondary)' }}>Unassigned</strong>.
            </div>
          </div>
        </div>
      </div>

      {/* Statement type selector (v5.3) */}
      <div className="card card-sm" style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          {statementType === 'bank'
            ? <Landmark size={18} style={{ color: 'var(--accent-hover)', flexShrink: 0 }} />
            : <CreditCard size={18} style={{ color: 'var(--accent-hover)', flexShrink: 0 }} />}
          <div style={{ flex: 1, minWidth: 200 }}>
            <label style={{ marginBottom: 6 }}>Statement type</label>
            <div className="toggle-group" role="group" aria-label="Statement type">
              <button
                type="button"
                className={`toggle-btn${statementType === 'card' ? ' active' : ''}`}
                onClick={() => setStatementType('card')}
              >
                Credit card
              </button>
              <button
                type="button"
                className={`toggle-btn${statementType === 'bank' ? ' active' : ''}`}
                onClick={() => setStatementType('bank')}
              >
                Bank account
              </button>
            </div>
            {autoDetectedBank && statementType === 'bank' && (
              <div style={{ fontSize: 12, color: 'var(--accent-hover)', marginTop: 6 }}>
                Detected a balance column — pre-selected Bank account. You can override this.
              </div>
            )}
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.5 }}>
              Bank/checking: money out is spending, money in is income. Credit card: charges are spending.
            </div>
          </div>
        </div>
      </div>

      {/* Drop zone */}
      <div
        className={`dropzone${dragOver ? ' drag-over' : ''}`}
        onDrop={onDrop}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
      >
        <div className="dropzone-icon">
          <Upload size={40} />
        </div>
        {file ? (
          <>
            <div className="dropzone-title" style={{ color: 'var(--accent-hover)' }}>
              {file.name}
            </div>
            <div className="dropzone-sub">
              {(file.size / 1024).toFixed(1)} KB · Click to change file
            </div>
          </>
        ) : (
          <>
            <div className="dropzone-title">Drop your CSV file here</div>
            <div className="dropzone-sub">or click to browse · .csv files only</div>
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.CSV,text/csv"
          style={{ display: 'none' }}
          onChange={onInputChange}
        />
      </div>

      {/* Error */}
      {error && (
        <div style={{
          marginTop: 16,
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '12px 16px',
          background: 'var(--danger-dim)',
          border: '1px solid rgba(244,63,94,0.3)',
          borderRadius: 'var(--radius-sm)',
          color: 'var(--danger)',
          fontSize: 14,
        }}>
          <XCircle size={18} />
          {error}
        </div>
      )}

      {/* Import Button */}
      {file && !result && (
        <div style={{ marginTop: 20, display: 'flex', gap: 10 }}>
          <button
            className="btn btn-primary"
            onClick={() => void handleImport()}
            disabled={importing}
          >
            {importing ? (
              <>
                <span className="spinner" style={{ width: 15, height: 15 }} />
                Importing…
              </>
            ) : (
              <>
                <Upload size={15} />
                Import Transactions
              </>
            )}
          </button>
          <button
            className="btn btn-secondary"
            onClick={clearResult}
          >
            Cancel
          </button>
        </div>
      )}

      {/* Results */}
      {result && (
        <div className="import-results">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ fontWeight: 600, fontSize: 15, color: 'var(--text-primary)' }}>
              Import Complete
            </div>
            <button className="btn btn-ghost btn-icon btn-sm" onClick={clearResult}>
              <X size={16} />
            </button>
          </div>

          <div style={{ fontSize: 14, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            Imported <strong style={{ color: 'var(--text-primary)' }}>{result.imported}</strong>{' '}
            transaction{result.imported === 1 ? '' : 's'}
            {result.transfers > 0 && (
              <> ({result.transfers} payment/transfer row{result.transfers === 1 ? '' : 's'} excluded from spending)</>
            )}
            , {result.skipped} skipped
            {result.errors.length > 0 && <>, {result.errors.length} with errors</>}
            {' '}into <strong style={{ color: 'var(--text-primary)' }}>{importedAccount || 'Unassigned'}</strong>.
          </div>

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <div className="import-stat success" style={{ flex: 1, minWidth: 130 }}>
              <CheckCircle size={18} />
              <span><strong>{result.imported}</strong> imported</span>
            </div>
            <div className="import-stat neutral" style={{ flex: 1, minWidth: 130 }}>
              <ArrowLeftRight size={18} />
              <span><strong>{result.transfers}</strong> transfers</span>
            </div>
            <div className="import-stat warning" style={{ flex: 1, minWidth: 130 }}>
              <Eye size={18} />
              <span><strong>{result.needs_review}</strong> need review</span>
            </div>
            <div className="import-stat neutral" style={{ flex: 1, minWidth: 130 }}>
              <AlertTriangle size={18} />
              <span><strong>{result.skipped}</strong> skipped</span>
            </div>
            {result.errors.length > 0 && (
              <div className="import-stat danger" style={{ flex: 1, minWidth: 130 }}>
                <XCircle size={18} />
                <span><strong>{result.errors.length}</strong> errors</span>
              </div>
            )}
          </div>

          {(result.needs_review > 0 || aiEnabled) && (
            <div className="review-cta">
              {result.needs_review > 0 && (
                <Link to="/transactions?needs_review=true" className="review-cta-link">
                  <Eye size={15} />
                  {result.needs_review} row{result.needs_review === 1 ? '' : 's'} need review →
                </Link>
              )}
              {aiEnabled && (
                <button className="btn btn-secondary btn-sm" onClick={() => void handleAutoCategorize()} disabled={categorizing}>
                  {categorizing ? <span className="spinner" style={{ width: 13, height: 13 }} /> : <Sparkles size={14} />}
                  Auto-categorize with AI
                </button>
              )}
            </div>
          )}
          {aiMsg && (
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 6 }}>
              <Sparkles size={14} style={{ color: 'var(--gold)' }} />
              {aiMsg}
            </div>
          )}

          {result.transfers > 0 && (
            <div style={{ fontSize: 12.5, color: 'var(--text-muted)', lineHeight: 1.6 }}>
              Transfers are payments between your own accounts (e.g. a credit-card payment). They appear in your
              transaction list but are excluded from spending totals so purchases aren't double-counted. You can
              change any row's type from the Transactions page.
            </div>
          )}

          {result.errors.length > 0 && (
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8 }}>
                Error Details
              </div>
              <div className="error-table">
                <table>
                  <thead>
                    <tr>
                      <th style={{ width: 80 }}>Row</th>
                      <th>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.errors.map((err, i) => (
                      <tr key={i}>
                        <td style={{ color: 'var(--text-muted)' }}>#{err.row}</td>
                        <td style={{ color: 'var(--danger)' }}>{err.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <button className="btn btn-secondary btn-sm" onClick={clearResult} style={{ alignSelf: 'flex-start' }}>
            Import Another File
          </button>
        </div>
      )}

      {/* Previous imports (v5.2) */}
      <div className="card" style={{ marginTop: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
          <History size={18} style={{ color: 'var(--accent-hover)' }} />
          <div style={{ fontWeight: 600, fontSize: 15 }}>Previous imports</div>
        </div>

        {batchMsg && (
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12 }}>
            <Check size={14} style={{ color: 'var(--green)' }} />
            {batchMsg}
          </div>
        )}

        {imports.length === 0 ? (
          <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>
            No imports yet. Files you import will appear here so you can reassign or undo them.
          </div>
        ) : (
          <div className="import-history">
            {imports.map((b) => (
              <ImportRow
                key={b.id}
                batch={b}
                accounts={accounts}
                busy={batchBusyId === b.id}
                confirming={deleteConfirmId === b.id}
                onReassign={(acct) => void handleReassign(b, acct)}
                onAskDelete={() => setDeleteConfirmId(b.id)}
                onCancelDelete={() => setDeleteConfirmId(null)}
                onConfirmDelete={() => void handleDeleteImport(b.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Import history row (v5.2) ───────────────────────────────────────────────

function ImportRow({
  batch, accounts, busy, confirming, onReassign, onAskDelete, onCancelDelete, onConfirmDelete,
}: {
  batch: ImportBatch
  accounts: string[]
  busy: boolean
  confirming: boolean
  onReassign: (account: string) => void
  onAskDelete: () => void
  onCancelDelete: () => void
  onConfirmDelete: () => void
}) {
  const [acct, setAcct] = useState(batch.account ?? '')
  const dirty = (acct.trim() || null) !== (batch.account ?? null)
  const when = new Date(batch.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })

  return (
    <div className="import-row">
      <div className="import-row-main">
        <div className="import-row-file">
          <FileText size={15} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
          <span className="import-row-name" title={batch.filename}>{batch.filename}</span>
          {batch.statement_type && (
            <span className={`badge badge-statement badge-statement-${batch.statement_type}`}>
              {batch.statement_type === 'bank' ? 'bank' : 'card'}
            </span>
          )}
        </div>
        <div className="import-row-meta">
          <span className="mono">{when}</span>
          <span>·</span>
          <span>{batch.imported} imported</span>
          <span>·</span>
          <span>{batch.transfers} transfers</span>
          <span>·</span>
          <span>{batch.needs_review} need review</span>
        </div>
      </div>

      <div className="import-row-actions">
        <input
          type="text"
          list={`imp-accts-${batch.id}`}
          className="import-row-account"
          placeholder="Unassigned"
          value={acct}
          onChange={(e) => setAcct(e.target.value)}
          aria-label="Reassign account"
          autoComplete="off"
        />
        <datalist id={`imp-accts-${batch.id}`}>
          {accounts.map((a) => <option key={a} value={a} />)}
        </datalist>
        <button
          className="btn btn-secondary btn-sm"
          onClick={() => onReassign(acct)}
          disabled={busy || !dirty}
          title={dirty ? 'Reassign this import to the account above' : 'Change the account to reassign'}
        >
          {busy && !confirming ? <span className="spinner" style={{ width: 12, height: 12 }} /> : null}
          Reassign
        </button>
        {confirming ? (
          <>
            <button className="btn btn-danger btn-sm" onClick={onConfirmDelete} disabled={busy}>
              {busy ? <span className="spinner" style={{ width: 12, height: 12 }} /> : 'Confirm undo'}
            </button>
            <button className="btn btn-ghost btn-sm" onClick={onCancelDelete}>Cancel</button>
          </>
        ) : (
          <button
            className="btn btn-ghost btn-icon btn-sm"
            onClick={onAskDelete}
            title="Undo this import (deletes its transactions)"
            style={{ color: 'var(--danger)' }}
          >
            <Trash2 size={14} />
          </button>
        )}
      </div>
    </div>
  )
}
