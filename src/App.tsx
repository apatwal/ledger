import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import LedgerLoader from './components/LedgerLoader'
import Layout from './components/Layout'
import { AccountSelectionProvider } from './lib/accountSelection'

// Route-level code splitting: each page (and its heavy deps — Recharts on the
// Dashboard especially) loads on demand, keeping the initial bundle small.
// Layout stays eager because it frames every route.
const Dashboard = lazy(() => import('./components/Dashboard'))
const Transactions = lazy(() => import('./components/Transactions'))
const CsvImport = lazy(() => import('./components/CsvImport'))
const Rules = lazy(() => import('./components/Rules'))
const Holdings = lazy(() => import('./components/Holdings'))
const Budget = lazy(() => import('./components/Budget'))

// On-theme placeholder shown while a route chunk is fetched.
function RouteFallback() {
  return <LedgerLoader variant="inline" />
}

export default function App() {
  return (
    <AccountSelectionProvider>
      <Layout>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/transactions" element={<Transactions />} />
            <Route path="/investments" element={<Holdings />} />
            <Route path="/budget" element={<Budget />} />
            <Route path="/rules" element={<Rules />} />
            <Route path="/import" element={<CsvImport />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </Suspense>
      </Layout>
    </AccountSelectionProvider>
  )
}
