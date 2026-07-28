import { test, expect, type Page } from '@playwright/test'

// ─── Accounts "wallet" UI e2e ──────────────────────────────────────────────
//
// The isolated e2e stack (see playwright.config.ts) runs against a THROWAWAY
// empty SQLite DB, so /accounts would normally show the empty-wallet state.
// We STUB the two GETs the page makes on mount so the fanned card wallet
// renders deterministically:
//   • GET /api/plaid/status   → a PlaidStatus fixture (3 banks / 4 accounts)
//   • GET /api/stats/net-worth → a NetWorth fixture
// Routes are registered BEFORE navigating and match the browser-side URLs
// (BASE = '/api' in src/lib/api.ts, proxied to :8001).
//
// The desktop FAN layout (with hover-lift) needs a viewport > 1180px. The
// default Desktop Chrome project is 1280px wide, so we do NOT touch the
// viewport — shrinking it would fall back to the mobile column variant where
// the lift is disabled (foot always visible), invalidating these tests.

// ── Fixtures (shapes per src/lib/types.ts) ──────────────────────────────────

const PLAID_STATUS = {
  configured: true,
  env: 'production',
  products: ['transactions', 'investments'],
  items: [
    {
      id: 1,
      institution_name: 'Chase',
      institution_logo: null,
      institution_color: '#0a4595',
      last_synced_at: '2026-07-23T12:00:00Z',
      status: 'active',
      accounts: [
        {
          account_id: 'chase-cc-2875',
          name: 'Chase Sapphire Preferred',
          mask: '2875',
          type: 'credit',
          subtype: 'credit card',
          app_account: 'Chase Sapphire',
          available: null,
          current: 389.57,
          currency: 'USD',
        },
      ],
    },
    {
      id: 2,
      institution_name: 'Bank of America',
      institution_logo: null,
      institution_color: '#c8102e',
      last_synced_at: '2026-07-23T12:00:00Z',
      status: 'active',
      accounts: [
        {
          account_id: 'bofa-chk-7383',
          name: 'Advantage Banking',
          mask: '7383',
          type: 'depository',
          subtype: 'checking',
          app_account: 'BofA Everyday Checking',
          available: 4379.68,
          current: 4379.68,
          currency: 'USD',
        },
      ],
    },
    {
      id: 3,
      institution_name: 'Robinhood',
      institution_logo: null,
      institution_color: '#00c805',
      last_synced_at: '2026-07-23T12:00:00Z',
      status: 'active',
      accounts: [
        {
          account_id: 'rh-brk-9673',
          name: 'Individual',
          mask: '9673',
          type: 'investment',
          subtype: 'brokerage',
          app_account: 'Robinhood Brokerage',
          available: null,
          current: 5543.19,
          currency: 'USD',
        },
        {
          account_id: 'rh-cry-9857',
          name: 'Crypto',
          mask: '9857',
          type: 'investment',
          subtype: 'crypto',
          app_account: 'Robinhood Crypto',
          available: null,
          current: 31.99,
          currency: 'USD',
        },
      ],
    },
  ],
}

const NET_WORTH = {
  net_worth: 9565.29,
  assets: 9954.86,
  liabilities: 389.57,
  currency: 'USD',
  as_of: '2026-07-23',
}

// ── Helpers ─────────────────────────────────────────────────────────────────

// Number of card inners currently "lifted" out of the pocket. The lift is a
// translateY(-116px) on `.wallet-card-inner`; at rest the inner only carries a
// small fan rotation (m42 ≈ 0). Detect a lift via the matrix' m42 (Y-translate).
function countLifted(page: Page): Promise<number> {
  return page.evaluate(() => {
    const inners = Array.from(document.querySelectorAll('.wallet-card-inner'))
    return inners.filter((el) => {
      const t = getComputedStyle(el as Element).transform
      if (!t || t === 'none') return false
      const m = new DOMMatrixReadOnly(t)
      return m.m42 < -50
    }).length
  })
}

// Is the inner of the card at `index` lifted?
function cardLifted(page: Page, index: number): Promise<boolean> {
  return page
    .locator('.wallet-card')
    .nth(index)
    .locator('.wallet-card-inner')
    .evaluate((el) => {
      const t = getComputedStyle(el).transform
      if (!t || t === 'none') return false
      return new DOMMatrixReadOnly(t).m42 < -50
    })
}

// Computed opacity of a card's foot (per-card actions). 0 = tucked/hidden,
// 1 = revealed. Playwright's toBeVisible() ignores opacity, so we read it.
function footOpacity(page: Page, index: number): Promise<string> {
  return page
    .locator('.wallet-card')
    .nth(index)
    .locator('.wallet-card-foot')
    .evaluate((el) => getComputedStyle(el).opacity)
}

// Hover a specific card by index, targeting the exposed left strip so we land
// on that card and not a higher-z sibling that overlaps its right side. Cards
// overlap by --overlap (168) of --card-w (272), leaving ~104px exposed on the
// left, so x ≈ 35 reliably hits this card. We move the raw mouse (rather than
// locator.hover()) so a currently-lifted card can't deadlock the occlusion
// check: moving off it un-lifts it, freeing the target point. The `.wallet-card`
// hit area is stationary (only the inner transforms), so its box is stable.
async function hoverCard(page: Page, index: number) {
  const box = await page.locator('.wallet-card').nth(index).boundingBox()
  if (!box) throw new Error(`no bounding box for card ${index}`)
  await page.mouse.move(box.x + 35, box.y + 150)
}

// Park the mouse well away from the wallet so nothing is hovered.
async function mouseAway(page: Page) {
  await page.mouse.move(2, 2)
}

test.describe('accounts wallet', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/plaid/status', (route) =>
      route.fulfill({ json: PLAID_STATUS }),
    )
    await page.route('**/api/stats/net-worth', (route) =>
      route.fulfill({ json: NET_WORTH }),
    )
    await page.goto('/accounts')
    // Wallet has rendered once all four fixture cards are present.
    await expect(page.locator('.wallet-card')).toHaveCount(4)
  })

  // 1 — one `.wallet-card` per fixture account.
  test('renders one card per linked account', async ({ page }) => {
    await expect(page.locator('.wallet-card')).toHaveCount(4)
    // The billfold pocket is present in the desktop fan layout.
    await expect(page.locator('.wallet-pocket')).toBeVisible()
    // Each fixture account's name shows on its card.
    await expect(page.locator('.wallet-card-nick', { hasText: 'Chase Sapphire' })).toBeVisible()
    await expect(page.locator('.wallet-card-nick', { hasText: 'BofA Everyday Checking' })).toBeVisible()
    await expect(page.locator('.wallet-card-nick', { hasText: 'Robinhood Brokerage' })).toBeVisible()
    await expect(page.locator('.wallet-card-nick', { hasText: 'Robinhood Crypto' })).toBeVisible()
  })

  // 2 — net-worth hero shows STATIC figures (not the animated count-up number).
  test('net-worth hero shows the static linked summary', async ({ page }) => {
    await expect(page.getByText('Net worth')).toBeVisible()
    // The linked line is fully static & deterministic: 3 banks, 4 accounts.
    // (The hero rounds assets/liabilities to whole dollars, and the net_worth
    // figure count-ups — so we assert this static line to avoid flakiness.)
    await expect(page.getByText('3 banks · 4 accounts linked')).toBeVisible()
  })

  // 3 — hovering a card reveals THAT card's actions and lifts exactly it.
  test('hovering a card lifts only it and reveals its actions', async ({ page }) => {
    // A different card's actions start hidden.
    expect(await footOpacity(page, 0)).toBe('0')

    await hoverCard(page, 1) // Bank of America
    await expect.poll(() => countLifted(page)).toBe(1)
    expect(await cardLifted(page, 1)).toBe(true)

    // The hovered card's foot is revealed; a non-hovered card's stays hidden.
    await expect.poll(() => footOpacity(page, 1)).toBe('1')
    expect(await footOpacity(page, 0)).toBe('0')

    // Its Sync + Disconnect actions are present in the foot.
    const foot = page.locator('.wallet-card').nth(1).locator('.wallet-card-foot')
    await expect(foot.getByRole('button', { name: 'Sync' })).toBeVisible()
    await expect(foot.getByRole('button', { name: /Disconnect Bank of America/i })).toBeVisible()
  })

  // 4 — hovering the black wallet body (pocket) lifts zero cards.
  test('hovering the pocket lifts no cards', async ({ page }) => {
    await page.locator('.wallet-pocket').hover()
    await expect.poll(() => countLifted(page)).toBe(0)
  })

  // 5 — REGRESSION: a mouse CLICK must not strand a card lifted. Click one card
  // (mouse focus → not :focus-visible), then hover a DIFFERENT card: exactly one
  // is lifted (the hovered one), never two. Move away → zero lifted.
  test('clicking a card then hovering another lifts exactly one', async ({ page }) => {
    // Click the body of the first card (upper area, away from the foot buttons).
    await page.locator('.wallet-card').nth(0).click({ position: { x: 35, y: 160 } })

    // THE BUG: a mouse click used to leave the card stranded-lifted (focus-within).
    // With the :focus-visible fix, moving the pointer off it must un-lift it —
    // so nothing is lifted here. (Under the bug this would stay at 1.)
    await mouseAway(page)
    await expect.poll(() => countLifted(page)).toBe(0)

    // Now hover a DIFFERENT card: exactly one lifts — never two (the clicked
    // card must not still be stranded).
    await hoverCard(page, 2) // Robinhood Brokerage
    await expect.poll(() => countLifted(page)).toBe(1)
    expect(await cardLifted(page, 2)).toBe(true)
    expect(await cardLifted(page, 0)).toBe(false)

    // Moving the mouse away leaves nothing lifted.
    await mouseAway(page)
    await expect.poll(() => countLifted(page)).toBe(0)
  })

  // 6 — the account name is not vertically clipped on a lifted card.
  test('account name is not vertically clipped when lifted', async ({ page }) => {
    await hoverCard(page, 1)
    await expect.poll(() => countLifted(page)).toBe(1)

    const nick = page.locator('.wallet-card').nth(1).locator('.wallet-card-nick')
    const clipped = await nick.evaluate(
      (el) => el.scrollHeight > el.clientHeight + 1,
    )
    expect(clipped).toBe(false)
  })
})
