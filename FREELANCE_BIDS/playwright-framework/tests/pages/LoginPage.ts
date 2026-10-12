import { Page } from '@playwright/test';
import { BasePage } from './BasePage';

/**
 * LoginPage — a concrete Page Object.
 *
 * Pattern: locators are private; behavior is exposed as intent methods
 * (fillCredentials, submit). Tests never touch selectors directly, so a UI
 * refactor is a one-file fix here instead of a hunt across many specs.
 *
 * Selectors use the stable `data-testid` contract (with an accessible fallback)
 * rather than brittle CSS chains — the standard for scalable Playwright suites.
 */
export class LoginPage extends BasePage {
  private readonly email: ReturnType<Page['getByTestId']>;
  private readonly password: ReturnType<Page['getByTestId']>;
  private readonly submitButton: ReturnType<Page['getByRole']>;
  private readonly error: ReturnType<Page['getByTestId']>;

  constructor(page: Page) {
    super(page);
    this.email = page.getByTestId('email-input');
    this.password = page.getByTestId('password-input');
    this.submitButton = page.getByRole('button', { name: /log ?in|sign ?in/i });
    this.error = page.getByTestId('login-error');
  }

  async open(): Promise<void> {
    await this.goto('/login');
  }

  async fillCredentials(email: string, password: string): Promise<void> {
    await this.safeFill(this.email, email);
    await this.safeFill(this.password, password);
  }

  async submit(): Promise<void> {
    await this.safeClick(this.submitButton);
  }

  /** One-call login used by many specs. */
  async login(email: string, password: string): Promise<void> {
    await this.open();
    await this.fillCredentials(email, password);
    await this.submit();
  }

  async errorMessage(): Promise<string> {
    await this.expectVisible(this.error);
    return (await this.error.textContent()) ?? '';
  }
}
