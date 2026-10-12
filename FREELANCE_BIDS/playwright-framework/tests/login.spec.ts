import { test, expect } from './fixtures';
import { users } from './fixtures/data';

/**
 * Login UI spec — written against the fixtures, NOT against selectors.
 *
 * This is the whole point of the framework: the test reads like a product
 * requirement ("admin can log in", "bad password is rejected") and never
 * touches a CSS class. If the app renames a button, only LoginPage.ts changes.
 */
test.describe('Login', () => {
  test('admin user can log in and land on the dashboard', async ({ login, page }) => {
    await login.login(users.admin.email, users.admin.password);

    // Navigation assertion via the base page helper (no arbitrary sleep).
    await expect(page).toHaveURL(/dashboard|home/);
  });

  test('a wrong password shows the inline error, not a redirect', async ({ login }) => {
    await login.login(users.admin.email, 'definitely-wrong-password');

    await expect(await login.errorMessage()).toMatch(/invalid|incorrect|wrong/i);
  });

  test('member user can log in', async ({ login, page }) => {
    await login.login(users.member.email, users.member.password);
    await expect(page).toHaveURL(/dashboard|home/);
  });
});
