import { useQuery } from '@tanstack/react-query'
import { TrendingUp, LineChart } from 'lucide-react'
import { getHoldings } from '../lib/api'

function fmtMoney(n: number | null | undefined, currency?: string | null): string {
  if (n === null || n === undefined) return '—'
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency || 'USD',
      maximumFractionDigits: 2,
    }).format(n)
  } catch {
    return `$${n.toFixed(2)}`
  }
}

function fmtQty(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 }).format(n)
}

export default function Holdings() {
  const { data: holdings = [], isPending, error: queryError } = useQuery({
    queryKey: ['plaid', 'holdings'],
    queryFn: getHoldings,
  })
  const loading = isPending
  // A 503 means Plaid isn't configured — a "feature off" state, distinct from a
  // real error or "no holdings yet".
  const errMsg = queryError instanceof Error ? queryError.message : queryError ? 'Could not load holdings' : null
  const disabled = errMsg?.startsWith('503') ?? false
  const error = disabled ? null : errMsg

  const total = holdings.reduce((sum, h) => sum + (h.value ?? 0), 0)

  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Portfolio</div>
        <h1 className="page-title">Investments</h1>
        <p className="page-subtitle">Positions held across your linked investment accounts.</p>
      </div>

      {loading ? (
        <div className="loading-state">
          <div className="spinner spinner-lg" />
          Loading holdings…
        </div>
      ) : disabled ? (
        <div className="card holdings-disabled">
          <div className="plaid-head">
            <LineChart size={18} style={{ color: 'var(--text-muted)' }} />
            <div style={{ fontWeight: 600, fontSize: 15, color: 'var(--text-secondary)' }}>
              Investment sync is off
            </div>
            <span className="badge badge-transfer" style={{ marginLeft: 'auto' }}>Not configured</span>
          </div>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.7, margin: 0 }}>
            Connect a bank with an investment account (via Plaid) to see your holdings here.
            Bank sync isn't enabled on this server.
          </p>
        </div>
      ) : error ? (
        <div className="error-state" style={{ padding: 32 }}>
          <TrendingUp size={28} />
          <div>{error}</div>
        </div>
      ) : holdings.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">
            <LineChart size={40} />
          </div>
          <div>No investment holdings found on your linked accounts.</div>
        </div>
      ) : (
        <>
          <section className="holdings-summary">
            <div className="holdings-summary-label">Total portfolio value</div>
            <div className="holdings-summary-figure mono">{fmtMoney(total)}</div>
            <div className="holdings-summary-sub">
              {holdings.length} position{holdings.length === 1 ? '' : 's'}
            </div>
          </section>

          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Security</th>
                  <th>Ticker</th>
                  <th>Account</th>
                  <th style={{ textAlign: 'right' }}>Quantity</th>
                  <th style={{ textAlign: 'right' }}>Price</th>
                  <th style={{ textAlign: 'right' }}>Value</th>
                </tr>
              </thead>
              <tbody>
                {holdings.map((h, i) => (
                  <tr key={`${h.ticker_symbol ?? h.security_name ?? 'row'}-${i}`}>
                    <td style={{ fontWeight: 600 }}>
                      {h.security_name ?? <span style={{ color: 'var(--text-muted)' }}>—</span>}
                      {h.institution && (
                        <div style={{ fontSize: 12, color: 'var(--text-muted)', fontWeight: 400 }}>
                          {h.institution}
                        </div>
                      )}
                    </td>
                    <td>
                      {h.ticker_symbol
                        ? <span className="badge badge-account">{h.ticker_symbol}</span>
                        : <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>—</span>}
                    </td>
                    <td style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
                      {h.account ?? '—'}
                    </td>
                    <td className="num">{fmtQty(h.quantity)}</td>
                    <td className="num">{fmtMoney(h.price, h.currency)}</td>
                    <td className="num" style={{ fontWeight: 600 }}>{fmtMoney(h.value, h.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
