import { ClerkProvider } from '@clerk/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import AuthGate from './components/AuthGate'
import IntroSplash from './components/IntroSplash'
import TopProgressBar from './components/TopProgressBar'
import './styles/globals.css'

// One client for the whole app. Reads are cached for 30s (fresh), kept for 5min
// after the last observer unmounts, and never refetch just because the window
// regained focus — this is a single-user ledger, not a live feed.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

// Gated: with a publishable key, the app runs behind Clerk sign-in and attaches
// the session token to API calls. Without one, it renders directly (local dev /
// unconfigured), matching the backend's gated auth.
const publishableKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined

// Match Clerk's components to the ledger/paper theme (see globals.css :root).
const clerkAppearance: React.ComponentProps<typeof ClerkProvider>['appearance'] = {
  variables: {
    colorPrimary: '#1f6b4a',
    colorForeground: '#1b1c18',
    colorMutedForeground: '#4c4e44',
    colorBackground: '#fbfbf6',
    colorInput: '#e8eae0',
    colorInputForeground: '#1b1c18',
    colorDanger: '#a8322e',
    colorSuccess: '#1f6b4a',
    borderRadius: '4px',
    fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
    fontFamilyButtons: "'IBM Plex Mono', ui-monospace, monospace",
  },
  elements: {
    card: {
      backgroundColor: '#fbfbf6',
      border: '1px solid #d6d8cb',
      boxShadow: '0 1px 2px rgba(27,28,24,0.05), 0 8px 24px -16px rgba(27,28,24,0.3)',
    },
    headerTitle: { fontFamily: "'Archivo', system-ui, sans-serif", letterSpacing: '-0.01em' },
    formButtonPrimary: {
      backgroundColor: '#1f6b4a',
      textTransform: 'none',
      fontWeight: 600,
      boxShadow: 'none',
    },
    socialButtonsBlockButton: { borderColor: '#d6d8cb' },
    footerActionLink: { color: '#1f6b4a' },
  },
}

const tree = publishableKey ? (
  <ClerkProvider publishableKey={publishableKey} afterSignOutUrl="/" appearance={clerkAppearance}>
    <AuthGate>
      <App />
    </AuthGate>
  </ClerkProvider>
) : (
  <App />
)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      {/* Slim top bar while any query is in flight — needs the query context. */}
      <TopProgressBar />
      <BrowserRouter>
        {tree}
        {/* Once-per-session pen intro; fixed overlay above both the Clerk and
            non-Clerk branches, lifts itself after one write cycle. */}
        <IntroSplash />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)
