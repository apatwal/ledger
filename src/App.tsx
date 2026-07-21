import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import { TopBar } from './components/TopProgressBar'
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

// First-ever load of a route downloads its chunk; show just the slim top bar
// (same visual as in-app fetches) rather than the heavy full-screen pen.
function RouteFallback() {
  return <TopBar />
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
