import { useEffect, useState } from 'react'
import LedgerLoader from './LedgerLoader'

/**
 * Once-per-browser-session intro. Loads are now fast enough that the pen
 * loader vanishes before the "Ledger" wordmark finishes writing, so this
 * overlay guarantees the whole animation plays exactly once — on the FIRST
 * load of a session — then never blocks again.
 *
 * It does NOT gate app render: the app mounts underneath immediately; this
 * fixed overlay simply covers it for one full write cycle (~1700ms), fades
 * out (~300ms), and unmounts. Subsequent loads (and reduced-motion users)
 * see nothing.
 */
const INTRO_KEY = 'ledger.introShown'
const HOLD_MS = 1700 // one full write cycle of the pen animation
const FADE_MS = 300 // opacity transition on exit (matches .intro-splash)

function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

export default function IntroSplash() {
  // Decide once, synchronously, whether the intro runs at all. Already shown
  // this session OR reduced-motion → mark the flag and never show (skip),
  // so reduced-motion users are not delayed.
  const [active] = useState(() => {
    if (typeof window === 'undefined') return false
    const alreadyShown = sessionStorage.getItem(INTRO_KEY) !== null
    if (alreadyShown || prefersReducedMotion()) {
      try {
        sessionStorage.setItem(INTRO_KEY, '1')
      } catch {
        /* storage unavailable — degrade to no intro */
      }
      return false
    }
    return true
  })

  const [visible, setVisible] = useState(active)
  const [leaving, setLeaving] = useState(false)

  useEffect(() => {
    if (!active) return
    let fadeTimer: ReturnType<typeof setTimeout> | undefined
    // Hold the overlay for the full write cycle, then begin the fade.
    const holdTimer = setTimeout(() => {
      setLeaving(true)
      fadeTimer = setTimeout(() => {
        setVisible(false)
        try {
          sessionStorage.setItem(INTRO_KEY, '1')
        } catch {
          /* ignore */
        }
      }, FADE_MS)
    }, HOLD_MS)
    return () => {
      clearTimeout(holdTimer)
      if (fadeTimer) clearTimeout(fadeTimer)
    }
  }, [active])

  if (!visible) return null

  return (
    <div
      className={`intro-splash${leaving ? ' intro-splash-leaving' : ''}`}
      aria-hidden={leaving}
    >
      <LedgerLoader variant="overlay" />
    </div>
  )
}
