import { useEffect, type ReactNode } from 'react'
import { Show, SignIn, ClerkLoading, ClerkLoaded, useAuth } from '@clerk/react'
import { setAuthTokenGetter } from '../lib/api'
import LedgerLoader from './LedgerLoader'
import LoginLedger from './LoginLedger'

/**
 * Wraps the routed app when Clerk is configured. It (1) registers the Clerk
 * session-token getter into the api layer so every request carries a bearer
 * token, and (2) gates the UI: signed-out users get a centered sign-in screen,
 * signed-in users get the app. When Clerk is NOT configured this component is
 * never mounted (see main.tsx), so the app runs unauthenticated as before.
 */
export default function AuthGate({ children }: { children: ReactNode }) {
  const { getToken } = useAuth()

  useEffect(() => {
    setAuthTokenGetter(() => getToken())
    return () => setAuthTokenGetter(null)
  }, [getToken])

  return (
    <>
      <ClerkLoading>
        <LedgerLoader variant="overlay" />
      </ClerkLoading>
      <ClerkLoaded>
        <Show when="signed-out">
          <div className="auth-split">
            <LoginLedger />
            <div className="login-signin">
              <div className="login-brand">
                <div className="login-brand-name">Expense Tracker</div>
                <div className="login-brand-sub">Personal Ledger</div>
                <div className="login-brand-help">Sign in to your ledger.</div>
              </div>
              <SignIn routing="hash" />
            </div>
          </div>
        </Show>
        <Show when="signed-in">{children}</Show>
      </ClerkLoaded>
    </>
  )
}
