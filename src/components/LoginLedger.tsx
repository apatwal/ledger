import { useEffect, useState } from 'react'

/**
 * The signature hero for the signed-out screen: a realistic double-entry
 * ledger "page" that PENS ITSELF on load — rows are written line by line,
 * the closing BALANCE counts up, and an "IN THE BLACK" tag pops in as the
 * final beat. Purely decorative (aria-hidden) so screen readers jump to the
 * sign-in card. Honors `prefers-reduced-motion` by rendering the final state
 * instantly.
 */

type Tone = 'expense' | 'income' | 'neutral'

interface LedgerRow {
  date: string
  desc: string
  debit?: string
  credit?: string
  balance: string
  tone: Tone
}

// A small monthly story. The running Balance column stays positive and lands
// exactly on FINAL_BALANCE — the figure the closing line counts up to.
const ROWS: LedgerRow[] = [
  { date: '07/01', desc: 'Opening balance', balance: '1,200.00', tone: 'neutral' },
  { date: '07/03', desc: 'Whole Foods', debit: '84.20', balance: '1,115.80', tone: 'expense' },
  { date: '07/05', desc: 'Payroll — ACME', credit: '3,200.00', balance: '4,315.80', tone: 'income' },
  { date: '07/09', desc: 'Rent', debit: '1,450.00', balance: '2,865.80', tone: 'expense' },
  { date: '07/14', desc: 'Transfer → Savings', debit: '400.00', balance: '2,465.80', tone: 'neutral' },
  { date: '07/18', desc: 'Refund — Amazon', credit: '38.40', balance: '2,504.20', tone: 'income' },
  { date: '07/22', desc: 'Coffee', debit: '4.20', balance: '2,500.00', tone: 'expense' },
]

const FINAL_BALANCE = 2500

// Timing for the one orchestrated moment.
const HEADER_DELAY = 260 // heavy rule + column headers settle first
const ROW_STAGGER = 92 // each row is "written" ~90ms after the last
const COUNT_DURATION = 700

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function formatMoney(n: number): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function todayStamp(): string {
  const d = new Date()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${mm}/${dd}/${d.getFullYear()}`
}

export default function LoginLedger() {
  const [reduced] = useState(prefersReducedMotion)
  const [displayBalance, setDisplayBalance] = useState(() => (prefersReducedMotion() ? FINAL_BALANCE : 0))
  const [showPill, setShowPill] = useState(() => prefersReducedMotion())
  // index of the row the "pen" is currently on; -1 once writing is done
  const [writingRow, setWritingRow] = useState(() => (prefersReducedMotion() ? -1 : 0))

  useEffect(() => {
    if (reduced) return

    const timers: number[] = []
    let raf = 0

    // advance the blinking pen caret down the rows in step with the stagger
    ROWS.forEach((_, i) => {
      timers.push(window.setTimeout(() => setWritingRow(i), HEADER_DELAY + i * ROW_STAGGER))
    })

    const doneWriting = HEADER_DELAY + ROWS.length * ROW_STAGGER
    timers.push(window.setTimeout(() => setWritingRow(-1), doneWriting))

    // closing balance counts up as the last beat, then the pill pops
    timers.push(
      window.setTimeout(() => {
        const start = performance.now()
        const tick = (now: number) => {
          const t = Math.min(1, (now - start) / COUNT_DURATION)
          const eased = 1 - Math.pow(1 - t, 3) // ease-out cubic
          setDisplayBalance(FINAL_BALANCE * eased)
          if (t < 1) {
            raf = requestAnimationFrame(tick)
          } else {
            setDisplayBalance(FINAL_BALANCE)
            setShowPill(true)
          }
        }
        raf = requestAnimationFrame(tick)
      }, doneWriting),
    )

    return () => {
      timers.forEach((id) => clearTimeout(id))
      if (raf) cancelAnimationFrame(raf)
    }
  }, [reduced])

  return (
    <div className="login-ledger" aria-hidden="true">
      <div className="login-ledger-page">
        {/* punch-hole margin down the far left */}
        <div className="login-ledger-holes">
          {Array.from({ length: 14 }).map((_, i) => (
            <span key={i} className="login-ledger-hole">
              ◦
            </span>
          ))}
        </div>

        <div className="login-ledger-body">
          <div className="login-ledger-head">
            <span className="login-ledger-title">General Ledger</span>
            <span className="login-ledger-asof">AS OF {todayStamp()}</span>
          </div>

          <div className="login-ledger-rule" />

          <div className="login-ledger-cols">
            <span>Date</span>
            <span>Description</span>
            <span className="num">Debit</span>
            <span className="num">Credit</span>
            <span className="num">Balance</span>
          </div>

          <div className="login-ledger-rows">
            {ROWS.map((row, i) => {
              return (
                <div
                  key={row.date}
                  className={`login-ledger-row${writingRow === i ? ' writing' : ''}`}
                  style={reduced ? undefined : { animationDelay: `${HEADER_DELAY + i * ROW_STAGGER}ms` }}
                >
                  <span className="num lr-date">{row.date}</span>
                  <span className="lr-desc">{row.desc}</span>
                  <span className={`num lr-debit${row.tone === 'expense' ? ' is-expense' : ''}`}>
                    {row.debit ?? '—'}
                  </span>
                  <span className={`num lr-credit${row.credit ? ' is-credit' : ''}`}>
                    {row.credit ?? '—'}
                  </span>
                  <span className="num lr-balance">{row.balance}</span>
                </div>
              )
            })}
          </div>

          <div className="login-ledger-rule heavy" />

          <div className="login-ledger-footing">
            <span className="login-ledger-footing-label">
              <span className="login-ledger-dot" /> Balance
            </span>
            <span className="login-ledger-total num">{formatMoney(displayBalance)}</span>
            <span className={`login-ledger-tag${showPill ? ' show' : ''}`}>In the black</span>
          </div>
        </div>
      </div>
    </div>
  )
}
