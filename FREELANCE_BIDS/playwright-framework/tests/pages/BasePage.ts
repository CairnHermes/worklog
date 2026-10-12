import { Page, Locator } from '@playwright/test';

/**
 * BasePage — the common, low-level building block every page object inherits.
 *
 * Why a base class?
 *  - One place for "slow-path" primitives (wait-stable, safe click/type) that
 *    need to behave identically across pages and browsers.
 *  - Keeps individual page objects tiny: they only declare locators + intent
 *    methods (login, add-to-cart), not the generic plumbing.
 *
 * Locator-first: every public method returns/accepts a Locator and never calls
 * page.waitForTimeout(). That is what makes the suite fast and stable.
 */
export abstract class BasePage {
  protected readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  /** Navigate and wait for the page to be interactive (no arbitrary sleep). */
  async goto(url: string): Promise<void> {
    await this.page.goto(url, { waitUntil: 'domcontentloaded' });
  }

  /**
   * Click once the element is stable (attached, visible, and not moving).
   * Replaces the brittle click-then-sleep pattern.
   */
  async safeClick(locator: Locator): Promise<void> {
    await locator.scrollIntoViewIfNeeded();
    await locator.click();
  }

  /** Type into a field after it is editable; optional clear-first for idempotence. */
  async safeFill(locator: Locator, value: string, clearFirst = true): Promise<void> {
    if (clearFirst) await locator.fill('');
    await locator.fill(value);
  }

  /** Assert a Locator becomes visible (default Playwright expect). */
  async expectVisible(locator: Locator): Promise<void> {
    await locator.waitFor({ state: 'visible' });
  }

  /** Grab the current URL — handy for navigation assertions. */
  url(): string {
    return this.page.url();
  }
}
