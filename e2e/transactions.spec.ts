import { test, expect } from '@playwright/test'

test.describe('transactions', () => {
  test('add an expense and see it in the ledger', async ({ page }) => {
    // Unique marker so the assertion is independent of any other data/tests.
    const marker = `E2E Coffee ${Date.now()}`

    await page.goto('/transactions')
    await page.getByRole('button', { name: 'Add entry' }).click()

    const modal = page.locator('.modal')
    await expect(modal).toBeVisible()

    await modal.getByRole('spinbutton', { name: 'Amount ($)' }).fill('12.50')
    await modal.getByRole('button', { name: 'expense', exact: true }).click()
    await modal.getByRole('combobox', { name: 'Category' }).selectOption('Dining')
    await modal.getByRole('textbox', { name: 'Description (optional)' }).fill(marker)
    await modal.getByRole('button', { name: 'Add Transaction' }).click()

    // Modal closes and the new row shows up in the table.
    await expect(modal).toBeHidden()
    await expect(page.getByText(marker)).toBeVisible()
  })

  test('exclude filter hides a type', async ({ page }) => {
    // Seed one income row so there is something to hide.
    const marker = `E2E Salary ${Date.now()}`
    await page.goto('/transactions')
    await page.getByRole('button', { name: 'Add entry' }).click()
    const modal = page.locator('.modal')
    await modal.getByRole('spinbutton', { name: 'Amount ($)' }).fill('2000')
    await modal.getByRole('button', { name: 'income', exact: true }).click()
    await modal.getByRole('combobox', { name: 'Category' }).selectOption('Salary')
    await modal.getByRole('textbox', { name: 'Description (optional)' }).fill(marker)
    await modal.getByRole('button', { name: 'Add Transaction' }).click()
    await expect(page.getByText(marker)).toBeVisible()

    // Open the Exclude control (a listbox popover) and hide the "Income" type.
    // Scope to the popover's listbox — the Type filter <select> also has an
    // "Income" option, so an unscoped role=option match is ambiguous.
    await page.getByRole('button', { name: /hidden/i }).click()
    const excludePopover = page.getByRole('listbox')
    await excludePopover.getByRole('option', { name: 'Income', exact: true }).click()
    await page.keyboard.press('Escape') // close the popover so it doesn't overlay the table

    // The income row is now filtered out of the ledger.
    await expect(page.getByText(marker)).toBeHidden()
  })
})
