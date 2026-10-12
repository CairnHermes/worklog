import { test as base } from '@playwright/test';
import { LoginPage } from '../pages/LoginPage';
import { users } from './data';

/**
 * Fixture layer — the heart of the "fixture-based" approach in the spec.
 *
 * Playwright's `test.extend` builds a DI container per test. Here we:
 *  - inject ready-made page objects (login, ...) so specs never `new` them;
 *  - inject a pre-authenticated storageState for tests that need a session;
 *  - give every test clean, isolated state with automatic teardown.
 *
 * A spec then reads like business logic:
 *
 *    test('admin can log in', async ({ login, authenticated }) => {
 *      await login.login(users.admin.email, users.admin.password);
 *      ...
 *    });
 */

type Fixtures = {
  /** unauthenticated login page object */
  login: LoginPage;
  /** a page already authenticated as the admin user (storageState injected) */
  authenticatedPage: import('@playwright/test').Page;
};

export const test = base.extend<Fixtures>({
  login: async ({ page }, use) => {
    await use(new LoginPage(page));
  },

  authenticatedPage: async ({ context }, use) => {
    // In a real project this would log in once and export a storageState file
    // (see tests/fixtures/auth.setup.ts). For the framework skeleton we simply
    // hand back a fresh context-bound page; swap in the persisted state when
    // the target app's login endpoint is wired.
    const page = await context.newPage();
    await use(page);
  },
});

export { expect } from '@playwright/test';
