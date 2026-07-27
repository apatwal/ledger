import PlaidConnect from './PlaidConnect'

// Accounts — the Plaid-first home for connecting banks and cards. Bank linking
// is the app's primary ingestion path now that CSV import is decommissioned, so
// it gets its own top-level page and nav entry rather than living on /import.
export default function Accounts() {
  return (
    <div>
      <div className="page-header">
        <div className="page-eyebrow">Your money</div>
        <h1 className="page-title">Accounts</h1>
        <p className="page-subtitle">
          Connect your banks and cards to sync transactions and balances automatically.
        </p>
      </div>

      <PlaidConnect />
    </div>
  )
}
