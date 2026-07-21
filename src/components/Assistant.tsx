import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Sparkles, X, ArrowUp, Check, ArrowRight } from 'lucide-react'
import { getAssistantStatus, assistantBudget } from '../lib/api'
import type { ChatMessage, BudgetChatResponse } from '../lib/types'

const SUGGESTIONS = [
  'How much did I spend on dining this year?',
  'Save $2,000 for a Japan trip by December',
  'Cap my Food & Drink spending at $400 a month',
]

// A rendered chat entry — the assistant may also have `created` budgets attached.
interface ChatEntry {
  role: 'user' | 'assistant'
  content: string
  created?: BudgetChatResponse['created']
}

function fmt(n: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(n)
}

function monthYear(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(`${iso}T00:00:00`)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-US', { month: 'short', year: 'numeric' })
}

function hasCreations(c?: BudgetChatResponse['created']): boolean {
  return !!c && (c.goals.length > 0 || c.category_limits.length > 0)
}

export default function Assistant() {
  const [enabled, setEnabled] = useState(false)
  const [open, setOpen] = useState(false)
  const [entries, setEntries] = useState<ChatEntry[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    getAssistantStatus()
      .then((s) => setEnabled(s.enabled))
      .catch(() => setEnabled(false))
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [entries, loading])

  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  async function send(text: string) {
    const trimmed = text.trim()
    if (!trimmed || loading) return
    setError(null)
    const next: ChatEntry[] = [...entries, { role: 'user', content: trimmed }]
    setEntries(next)
    setInput('')
    setLoading(true)
    try {
      // The budget endpoint answers like normal chat AND creates goals/limits when
      // the message expresses that intent — so it cleanly covers both cases.
      const history: ChatMessage[] = next.map((e) => ({ role: e.role, content: e.content }))
      const res = await assistantBudget(history)
      setEntries([...next, { role: 'assistant', content: res.reply, created: res.created }])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong')
    } finally {
      setLoading(false)
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void send(input)
    }
  }

  if (!enabled) return null

  return (
    <>
      {open && (
        <div className="assistant-panel" role="dialog" aria-label="Ledger assistant">
          <div className="assistant-header">
            <div className="assistant-title">
              <Sparkles size={15} />
              <span>Ask your ledger</span>
            </div>
            <button className="btn btn-ghost btn-icon btn-sm" onClick={() => setOpen(false)} aria-label="Close assistant">
              <X size={16} />
            </button>
          </div>

          <div className="assistant-body" ref={scrollRef}>
            {entries.length === 0 && (
              <div className="assistant-intro">
                <p>Ask anything about your spending, or tell me a savings goal or category limit and I'll set it up.</p>
                <div className="assistant-suggestions">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} className="assistant-chip" onClick={() => void send(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {entries.map((m, i) => (
              <div key={i}>
                <div className={`chat-row ${m.role}`}>
                  <div className={`chat-bubble ${m.role}`}>{m.content}</div>
                </div>
                {m.role === 'assistant' && hasCreations(m.created) && (
                  <div className="budget-confirm">
                    {m.created!.goals.map((g) => (
                      <div key={`g-${g.id}`} className="budget-confirm-item">
                        <Check size={13} className="budget-confirm-check" />
                        <span>
                          Created goal <strong>'{g.name}'</strong> — {fmt(g.target_amount)}
                          {g.target_date ? ` by ${monthYear(g.target_date)}` : ''}
                        </span>
                      </div>
                    ))}
                    {m.created!.category_limits.map((c) => (
                      <div key={`c-${c.id}`} className="budget-confirm-item">
                        <Check size={13} className="budget-confirm-check" />
                        <span>
                          <strong>{c.category}</strong> capped at {fmt(c.limit_amount)}/mo
                        </span>
                      </div>
                    ))}
                    <Link to="/budget" className="budget-confirm-link" onClick={() => setOpen(false)}>
                      View in Budget <ArrowRight size={13} />
                    </Link>
                  </div>
                )}
              </div>
            ))}

            {loading && (
              <div className="chat-row assistant">
                <div className="chat-bubble assistant typing">
                  <span /><span /><span />
                </div>
              </div>
            )}

            {error && <div className="assistant-error">{error}</div>}
          </div>

          <div className="assistant-input">
            <textarea
              ref={inputRef}
              rows={1}
              placeholder="Ask, or set a goal or limit…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
            />
            <button
              className="btn btn-primary btn-icon"
              onClick={() => void send(input)}
              disabled={loading || !input.trim()}
              aria-label="Send"
            >
              <ArrowUp size={16} />
            </button>
          </div>
        </div>
      )}

      <button
        className={`assistant-fab${open ? ' open' : ''}`}
        onClick={() => setOpen((o) => !o)}
        aria-label={open ? 'Close assistant' : 'Open assistant'}
      >
        {open ? <X size={20} /> : <Sparkles size={20} />}
      </button>
    </>
  )
}
