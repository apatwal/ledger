import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Sparkles, X, ArrowUp, Check, ArrowRight } from 'lucide-react'
import { getAssistantStatus, assistantChat } from '../lib/api'
import { invalidateLedger } from '../lib/queryKeys'
import ErrorNotice from './ErrorNotice'
import type { ChatMessage } from '../lib/types'

// Create-tool names the backend reports in `AssistantReply.tool_calls` when the
// assistant persists something this turn. Kept in sync with the backend tools.
const CREATE_RULE = 'create_categorization_rule'
const CREATE_GOAL = 'create_savings_goal'
const CREATE_BUDGET = 'create_category_budget'
const CREATE_TOOLS = [CREATE_RULE, CREATE_GOAL, CREATE_BUDGET]

const SUGGESTIONS = [
  'How much did I spend on dining this year?',
  'Save $2,000 for a NYC trip',
  'Cap my Food & Drink spending at $400 a month',
  'Always categorize Whole Foods as Groceries',
]

// A rendered chat entry. `tools` holds the CREATE tools that ran on an assistant
// turn (empty/absent when nothing was created) so we can show a confirmation.
interface ChatEntry {
  role: 'user' | 'assistant'
  content: string
  tools?: string[]
}

export default function Assistant() {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [entries, setEntries] = useState<ChatEntry[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const { data: status } = useQuery({ queryKey: ['assistant', 'status'], queryFn: getAssistantStatus })
  const enabled = status?.enabled ?? false

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
      // The chat endpoint answers questions AND can create rules / goals / budgets
      // via tool calls — it reports which tools ran in `tool_calls`.
      const history: ChatMessage[] = next.map((e) => ({ role: e.role, content: e.content }))
      const res = await assistantChat(history)
      // Code defensively: tool_calls may be undefined on read-only replies.
      const ran = res.tool_calls ?? []
      const created = ran.filter((t) => CREATE_TOOLS.includes(t))
      setEntries([...next, { role: 'assistant', content: res.reply, tools: created }])

      // A create tool ran → the ledger (and derived stats/duplicates) may have
      // changed, since a new rule can recategorize transactions. Refresh broadly,
      // then the specific lists the created entity shows up in.
      if (created.length > 0) {
        invalidateLedger(queryClient)
        if (ran.includes(CREATE_RULE)) void queryClient.invalidateQueries({ queryKey: ['rules'] })
        if (ran.includes(CREATE_GOAL)) void queryClient.invalidateQueries({ queryKey: ['budgets', 'goals'] })
        if (ran.includes(CREATE_BUDGET)) void queryClient.invalidateQueries({ queryKey: ['budgets', 'categories'] })
      }
    } catch (e) {
      setError(e)
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
                <p>
                  Ask anything about your spending, or tell me what to set up — a savings goal, a
                  monthly category budget, or a categorization rule — and I'll create it for you.
                </p>
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
                {m.role === 'assistant' && m.tools && m.tools.length > 0 && (
                  <div className="budget-confirm">
                    {m.tools.includes(CREATE_RULE) && (
                      <div className="budget-confirm-item">
                        <Check size={13} className="budget-confirm-check" />
                        <span>Created a categorization rule.</span>
                      </div>
                    )}
                    {m.tools.includes(CREATE_GOAL) && (
                      <div className="budget-confirm-item">
                        <Check size={13} className="budget-confirm-check" />
                        <span>Created a savings goal.</span>
                      </div>
                    )}
                    {m.tools.includes(CREATE_BUDGET) && (
                      <div className="budget-confirm-item">
                        <Check size={13} className="budget-confirm-check" />
                        <span>Set a monthly category budget.</span>
                      </div>
                    )}
                    {m.tools.includes(CREATE_RULE) && (
                      <Link to="/rules" className="budget-confirm-link" onClick={() => setOpen(false)}>
                        View in Rules <ArrowRight size={13} />
                      </Link>
                    )}
                    {(m.tools.includes(CREATE_GOAL) || m.tools.includes(CREATE_BUDGET)) && (
                      <Link to="/budget" className="budget-confirm-link" onClick={() => setOpen(false)}>
                        View in Budget <ArrowRight size={13} />
                      </Link>
                    )}
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

            {error != null && (
              <ErrorNotice compact title="The assistant couldn't answer" error={error} />
            )}
          </div>

          <div className="assistant-input">
            <textarea
              ref={inputRef}
              rows={1}
              placeholder="Ask, or set a goal, budget, or rule…"
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
