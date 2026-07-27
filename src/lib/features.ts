// Feature flags (source constants — flip + rebuild to toggle).
//
// CSV import was the pre-Plaid ingestion path. Now that the app is Plaid-first in
// production, the CSV UI is DECOMMISSIONED: this flag is off, so the Import nav
// link and /import route are hidden. All the code is intentionally KEPT intact
// and compiling (CsvImport.tsx, the api-client helpers, and the backend
// csv_import/imports routers) — set this to `true` and rebuild to restore the UI.
export const CSV_IMPORT_ENABLED: boolean = false
