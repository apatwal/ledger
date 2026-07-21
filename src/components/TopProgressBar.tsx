import { useIsFetching } from '@tanstack/react-query'

/**
 * The slim navigation-load signature. On in-app route/tab switches the full
 * pen loader is too heavy, so genuine loads now show only a thin bar pinned to
 * the very top edge. Cached pages render instantly (no bar at all).
 *
 * `<TopBar />` is the shared visual, reused by App's Suspense fallback (a
 * route chunk downloading for the first time). `<TopProgressBar />` drives it
 * off TanStack Query: visible only while data is in flight.
 */

// The bar itself — a fixed 3px track with an indeterminate ink→green sliding
// fill. Kept subtle and on-theme (tokens from :root).
export function TopBar() {
  return (
    <div
      className="top-progress"
      role="status"
      aria-live="polite"
      aria-label="Loading"
    >
      <span className="top-progress-fill" aria-hidden="true" />
    </div>
  )
}

export default function TopProgressBar() {
  const isFetching = useIsFetching()
  if (isFetching === 0) return null
  return <TopBar />
}
