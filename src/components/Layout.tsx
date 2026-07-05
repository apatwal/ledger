import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { LayoutDashboard, List, Upload, BookText, Wand2 } from 'lucide-react'
import { getHealth } from '../lib/api'
import Assistant from './Assistant'

interface LayoutProps {
  children: React.ReactNode
}

export default function Layout({ children }: LayoutProps) {
  const [online, setOnline] = useState<boolean | null>(null)

  useEffect(() => {
    getHealth()
      .then(() => setOnline(true))
      .catch(() => setOnline(false))
  }, [])

  return (
    <div className="app-layout">
      <aside className="sidebar">
        {/* Logo */}
        <div className="sidebar-logo">
          <div className="sidebar-logo-icon">
            <BookText size={18} strokeWidth={1.75} />
          </div>
          <div>
            <div className="sidebar-logo-text">Expense Tracker</div>
            <div className="sidebar-logo-sub">Personal Ledger</div>
          </div>
        </div>

        {/* Navigation */}
        <nav className="sidebar-nav">
          <div className="nav-section-label">Main</div>

          <NavLink
            to="/dashboard"
            className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
          >
            <LayoutDashboard className="nav-link-icon" />
            Dashboard
          </NavLink>

          <NavLink
            to="/transactions"
            className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
          >
            <List className="nav-link-icon" />
            Transactions
          </NavLink>

          <div className="nav-section-label">Tools</div>

          <NavLink
            to="/rules"
            className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
          >
            <Wand2 className="nav-link-icon" />
            Rules
          </NavLink>

          <NavLink
            to="/import"
            className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
          >
            <Upload className="nav-link-icon" />
            CSV Import
          </NavLink>
        </nav>

        {/* Footer / health */}
        <div className="sidebar-footer">
          <div className="health-label">
            <span className={`health-dot${online === false ? ' offline' : ''}`} />
            {online === null ? 'Connecting…' : online ? 'Ledger synced' : 'Working offline'}
          </div>
        </div>
      </aside>

      <main className="main-content">{children}</main>

      <Assistant />
    </div>
  )
}
