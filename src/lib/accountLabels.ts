// ─── Account display labels (investments-viz) ────────────────────────────────
// A cached read of user-set account nicknames plus a `displayLabel(label)` helper
// that returns the nickname when set, else the raw canonical label.
//
// IMPORTANT: this is a DISPLAY layer only. Every filter, query param, and cache
// key elsewhere still uses the raw canonical label — never the nickname.
import { useCallback, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getNicknames } from './api'
import type { AccountNickname } from './types'

export interface AccountLabels {
  nicknames: AccountNickname[]
  /** nickname when set (non-blank), otherwise the raw label. */
  displayLabel: (label: string) => string
  /** the nickname alone, or undefined when none is set — for edit pre-fill. */
  nicknameOf: (label: string) => string | undefined
  isLoading: boolean
}

export function useAccountLabels(): AccountLabels {
  // Shared, cached across the app. If the endpoint isn't up yet (backend still
  // building) the query errors and data falls back to [] — displayLabel then
  // just returns raw labels, so nothing crashes.
  const { data: nicknames = [], isPending } = useQuery({
    queryKey: ['accounts', 'nicknames'],
    queryFn: getNicknames,
  })

  const map = useMemo(() => {
    const m = new Map<string, string>()
    for (const n of nicknames) {
      const nick = (n.nickname ?? '').trim()
      if (nick) m.set(n.label, nick)
    }
    return m
  }, [nicknames])

  const displayLabel = useCallback((label: string) => map.get(label) ?? label, [map])
  const nicknameOf = useCallback((label: string) => map.get(label), [map])

  return { nicknames, displayLabel, nicknameOf, isLoading: isPending }
}
