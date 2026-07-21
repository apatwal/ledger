// ─── Query-key helpers (TanStack Query) ──────────────────────────────────────
// Centralizes the query-key scheme so every filtered read is cached under a key
// that encodes ALL of its inputs — cached data is never shown for the wrong
// filter — while writes can invalidate broad prefixes to refresh every variant.
import type { QueryClient } from '@tanstack/react-query'

// The global account selection is `string[] | undefined` (undefined = "all
// accounts", the server aggregates everything). Collapse it to a stable string
// so it can live inside a query key: order-independent, with a distinct sentinel
// for the "all" case (which must never collide with an explicit selection).
export function acctKey(accounts: string[] | undefined): string {
  return accounts && accounts.length ? [...accounts].sort().join(',') : '__all__'
}

// After a write that changes the ledger itself (transactions, imports, bank
// sync) every derived read may be stale — refresh them all via prefix match.
export function invalidateLedger(qc: QueryClient): void {
  for (const key of [
    ['transactions'],
    ['stats'],
    ['duplicates'],
    ['accounts'],
    ['plaid'],
    ['holdings'],
    ['budgets'],
  ]) {
    void qc.invalidateQueries({ queryKey: key })
  }
}
