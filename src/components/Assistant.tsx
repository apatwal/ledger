import { useEffect, useRef, useState } from 'react'
import { Sparkles, X, ArrowUp } from 'lucide-react'
import { getAssistantStatus, assistantChat } from '../lib/api'
import type { ChatMessage } from '../lib/types'

const SUGGESTIONS = [
  'How much did I spend on dining this year?',
  'What are my biggest expense categories?',
  'Am I in the black or the red?',
]

export default function Assistant() {
  const [enabled, setEnabled] = useState(false)
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
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
  }, [messages, loading])

  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  async function send(text: string) {
    const trimmed = text.trim()
    if (!trimmed || loading) return
    setError(null)
    const next: ChatMessage[] = [...messages, { role: 'user', content: trimmed }]
    setMessages(next)
    setInput('')
    setLoading(true)
    try {
      const res = await assistantChat(next)
      setMessages([...next, { role: 'assistant', content: res.reply }])
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
            {messages.length === 0 && (
              <div className="assistant-intro">
                <p>Ask anything about your spending. I read your real transactions to answer.</p>
                <div className="assistant-suggestions">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} className="assistant-chip" onClick={() => void send(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m, i) => (
              <div key={i} className={`chat-row ${m.role}`}>
                <div className={`chat-bubble ${m.role}`}>{m.content}</div>
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
              placeholder="Ask about your money…"
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
