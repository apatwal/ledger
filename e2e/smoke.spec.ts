import { test, expect } from '@playwright/test'

test.describe('smoke', () => {
  test('renders the dashboard past the auth gate', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/dashboard/)
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
    // KPI row is present
    await expect(page.getByText('Net balance')).toBeVisible()
    await expect(page.getByText('Savings rate')).toBeVisible()
  })

  test('primary navigation works', async ({ page }) => {
    await page.goto('/dashboard')

    await page.getByRole('link', { name: 'Transactions' }).click()
    await expect(page).toHaveURL(/\/transactions/)
    await expect(page.getByRole('heading', { name: 'Transactions' })).toBeVisible()

    await page.getByRole('link', { name: 'Budget' }).click()
    await expect(page).toHaveURL(/\/budget/)

    await page.getByRole('link', { name: 'Investments' }).click()
    await expect(page).toHaveURL(/\/investments/)
  })
})
