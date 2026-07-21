/**
 * The in-app loading signature: a pen that "writes" the wordmark "Ledger"
 * left-to-right over a ruled baseline, then holds, fades, and loops while
 * something is loading. Same motion as the pre-React boot splash in
 * index.html, so the hand-off from bundle-download → auth-bootstrap →
 * route-transition reads as one continuous idea.
 *
 * Technique (font-agnostic, no fragile glyph paths): the word is real text in
 * the display font, revealed left-to-right with an animated
 * `clip-path: inset(0 <100%→0> 0 0)`. A small pen-nib SVG rides the reveal
 * edge by animating `left: 0%→100%` (relative to the word's own width) in
 * sync, so the nib looks like it's laying down each letter. A ruled baseline
 * draws in beneath via `scaleX`, and a gold ink-dot sits at the nib tip.
 * All motion lives in globals.css; `prefers-reduced-motion: reduce` there
 * pins the finished word statically. See `.ledger-loader*` + keyframes.
 *
 * Decorative: the stage is aria-hidden and the whole thing is a polite live
 * region labelled "Loading" so assistive tech announces state without
 * narrating the animation.
 */

interface LedgerLoaderProps {
  /**
   * `overlay` — full-viewport centered on paper (auth bootstrap).
   * `inline`  — fills the routed content area (route transitions). Default.
   */
  variant?: 'overlay' | 'inline'
}

// The pen nib, tip at bottom-left so it can ride the writing edge / baseline.
function PenNib() {
  return (
    <span className="ledger-loader-nib" aria-hidden="true">
      <svg width="24" height="28" viewBox="0 0 24 28" fill="none">
        <path d="M19 1.6 22.4 5 8.2 19.2 4.2 21.4 5.9 16.6Z" fill="var(--ink)" />
        <path d="M5.9 16.6 4.2 21.4 8.2 19.2Z" fill="var(--paper-raised)" />
        <line x1="6" y1="16.5" x2="4.4" y2="21" stroke="var(--ink)" strokeWidth="1" />
      </svg>
      <span className="ledger-loader-ink" />
    </span>
  )
}

export default function LedgerLoader({ variant = 'inline' }: LedgerLoaderProps) {
  return (
    <div
      className={`ledger-loader ledger-loader-${variant}`}
      role="status"
      aria-live="polite"
      aria-label="Loading"
    >
      <div className="ledger-loader-stage" aria-hidden="true">
        <span className="ledger-loader-word">Ledger</span>
        <span className="ledger-loader-baseline" />
        <PenNib />
      </div>
      <span className="ledger-loader-caption">Loading</span>
    </div>
  )
}
