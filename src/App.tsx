import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './components/Dashboard'
import Transactions from './components/Transactions'
import CsvImport from './components/CsvImport'
import Rules from './components/Rules'
import Holdings from './components/Holdings'
import Budget from './components/Budget'
import { AccountSelectionProvider } from './lib/accountSelection'

export default function App() {
  return (
    <AccountSelectionProvider>
      <Layout>
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
      </Layout>
    </AccountSelectionProvider>
  )
}
