// ─── Global account selection (v9) ───────────────────────────────────────────
// A single source of truth for which accounts the whole app is looking at.
// The set of *deselected* labels is persisted to localStorage, so the default
// (nothing deselected) means "all accounts", and brand-new accounts that appear
// later are shown by default rather than silently hidden.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getAccounts, getPlaidStatus } from './api'

// Fetches (and de-dupes/caches) the account universe: transaction labels ∪ Plaid
// app_account labels, with balances where Plaid provides them.
async function fetchAccountUniverse(): Promise<AccountInfo[]> {
  const [acctRes, plaidRes] = await Promise.allSettled([getAccounts(), getPlaidStatus()])
  const byLabel = new Map<string, AccountInfo>()
  const add = (label: string | null | undefined, extra?: Partial<AccountInfo>) => {
    const l = (label ?? '').trim()
    if (!l) return
    const prev = byLabel.get(l) ?? { label: l }
    byLabel.set(l, { ...prev, ...extra, label: l })
  }
  if (acctRes.status === 'fulfilled') acctRes.value.forEach((l) => add(l))
  if (plaidRes.status === 'fulfilled') {
    for (const item of plaidRes.value.items ?? []) {
      for (const a of item.accounts ?? []) {
        add(a.app_account, { available: a.available, current: a.current, currency: a.currency })
      }
    }
  }
  return [...byLabel.values()].sort((x, y) => x.label.localeCompare(y.label))
}

const STORAGE_KEY = 'expense.deselectedAccounts'

// When the user deselects EVERY account, the intended result is "show nothing".
// An empty `accounts` list can't express that — the query serializer omits empty
// arrays, and the server treats an absent filter as "all accounts". So we send a
// sentinel label that no real account can equal; the server's `account IN (...)`
// filter then matches zero rows across every endpoint (stats, transactions,
// duplicates), which is exactly "none". Kept implausible on purpose.
const NONE_SELECTED_SENTINEL = '__none_selected__'

export interface AccountInfo {
  label: string
  available?: number | null
  current?: number | null
  currency?: string | null
}

export interface AccountSelectionValue {
  /** Every known account (transaction labels ∪ Plaid app_account labels), sorted. */
  accounts: AccountInfo[]
  /** Labels currently selected (a subset of `accounts`). */
  selected: string[]
  isSelected: (label: string) => boolean
  toggle: (label: string) => void
  selectAll: () => void
  clear: () => void
  /** True when nothing is deselected — i.e. the whole ledger is in view. */
  allSelected: boolean
  noneSelected: boolean
  /** For API calls: `undefined` when all are selected (server treats absent = all). */
  accountsParam: () => string[] | undefined
  balanceOf: (label: string) => AccountInfo | undefined
  loading: boolean
  refresh: () => void
}

const Ctx = createContext<AccountSelectionValue | null>(null)

function loadDeselected(): Set<string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return new Set()
    const arr = JSON.parse(raw) as unknown
    return new Set(Array.isArray(arr) ? arr.filter((x): x is string => typeof x === 'string') : [])
  } catch {
    return new Set()
  }
}

function saveDeselected(set: Set<string>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...set]))
  } catch {
    /* storage unavailable — selection just won't persist */
  }
}

export function AccountSelectionProvider({ children }: { children: ReactNode }) {
  const [deselected, setDeselected] = useState<Set<string>>(() => loadDeselected())

  // The account universe is a cached, deduped read shared across the app. Its
  // balances (and some labels) come from Plaid; the label universe also includes
  // accounts only seen in imported/manual transactions.
  const { data: accounts = [], isPending, refetch } = useQuery({
    queryKey: ['accounts', 'universe'],
    queryFn: fetchAccountUniverse,
  })
  const loading = isPending
  const refresh = useCallback(() => { void refetch() }, [refetch])

  // Persist whenever the deselected set changes.
  useEffect(() => { saveDeselected(deselected) }, [deselected])

  const allLabels = useMemo(() => accounts.map((a) => a.label), [accounts])
  const selected = useMemo(
    () => allLabels.filter((l) => !deselected.has(l)),
    [allLabels, deselected],
  )

  const isSelected = useCallback((label: string) => !deselected.has(label), [deselected])

  const toggle = useCallback((label: string) => {
    setDeselected((prev) => {
      const next = new Set(prev)
      if (next.has(label)) next.delete(label)
      else next.add(label)
      return next
    })
  }, [])

  const selectAll = useCallback(() => setDeselected(new Set()), [])
  const clear = useCallback(() => setDeselected(new Set(allLabels)), [allLabels])

  // "All selected" is defined against the known universe: none of the known
  // labels are in the deselected set.
  const allSelected = useMemo(
    () => allLabels.every((l) => !deselected.has(l)),
    [allLabels, deselected],
  )
  const noneSelected = selected.length === 0 && allLabels.length > 0

  const accountsParam = useCallback((): string[] | undefined => {
    if (noneSelected) return [NONE_SELECTED_SENTINEL] // explicit "none" → matches no rows
    if (allSelected) return undefined // absent → server aggregates everything
    return selected
  }, [noneSelected, allSelected, selected])

  const balanceOf = useCallback(
    (label: string) => accounts.find((a) => a.label === label),
    [accounts],
  )

  const value: AccountSelectionValue = {
    accounts,
    selected,
    isSelected,
    toggle,
    selectAll,
    clear,
    allSelected,
    noneSelected,
    accountsParam,
    balanceOf,
    loading,
    refresh,
  }

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAccountSelection(): AccountSelectionValue {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useAccountSelection must be used within an AccountSelectionProvider')
  return ctx
}
