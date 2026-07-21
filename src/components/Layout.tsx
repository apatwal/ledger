import { NavLink } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { LayoutDashboard, List, Upload, BookText, Wand2, LineChart, PiggyBank } from 'lucide-react'
import { UserButton } from '@clerk/react'
import { getHealth } from '../lib/api'
import Assistant from './Assistant'
import AccountSelect from './AccountSelect'

// When Clerk isn't configured, the app renders outside <ClerkProvider>, so the
// UserButton (which needs Clerk context) must be hidden.
const AUTH_ENABLED = Boolean(import.meta.env.VITE_CLERK_PUBLISHABLE_KEY)

interface LayoutProps {
  children: React.ReactNode
}

export default function Layout({ children }: LayoutProps) {
  // Health ping — cached; null while connecting, true when synced, false offline.
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth })
  const online: boolean | null = health.isSuccess ? true : health.isError ? false : null

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

          <NavLink
            to="/investments"
            className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
          >
            <LineChart className="nav-link-icon" />
            Investments
          </NavLink>

          <NavLink
            to="/budget"
            className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
          >
            <PiggyBank className="nav-link-icon" />
            Budget
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
            Import
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

      <main className="main-content">
        <div className="topbar">
          <div className="topbar-label">Viewing</div>
          <AccountSelect />
          {AUTH_ENABLED && (
            <div className="topbar-user">
              <UserButton />
            </div>
          )}
        </div>
        {children}
      </main>

      <Assistant />
    </div>
  )
}
