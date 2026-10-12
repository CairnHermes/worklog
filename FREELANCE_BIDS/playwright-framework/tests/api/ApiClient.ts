import { Page } from '@playwright/test';

/**
 * ApiClient — thin, typed wrapper over the app's HTTP API.
 *
 * The spec asks for "UI + API automation" in one framework. Sharing a single
 * client lets UI tests call the API for setup (create a user, seed an order)
 * and lets pure API tests live in the same repo with the same assertions.
 *
 * Deliberately dependency-free (fetch, which Playwright/Node both provide) so
 * the skeleton runs without extra installs.
 */
export interface ApiOptions {
  baseUrl: string;
  token?: string;
}

export class ApiClient {
  private readonly base: string;
  private readonly token?: string;

  constructor(opts: ApiOptions) {
    this.base = opts.baseUrl.replace(/\/$/, '');
    this.token = opts.token;
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    const h: Record<string, string> = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...extra,
    };
    if (this.token) h.Authorization = `Bearer ${this.token}`;
    return h;
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${this.base}${path}`, {
      method,
      headers: this.headers(),
      body: body !== undefined ? JSON.stringify(body) : null,
    });
    const text = await res.text();
    const data: T = text ? (JSON.parse(text) as T) : (undefined as T);
    if (!res.ok) {
      throw new ApiError(method, path, res.status, data);
    }
    return data;
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>('GET', path);
  }

  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>('POST', path, body);
  }

  put<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>('PUT', path, body);
  }

  delete<T>(path: string): Promise<T> {
    return this.request<T>('DELETE', path);
  }
}

export class ApiError extends Error {
  constructor(
    public method: string,
    public path: string,
    public status: number,
    public payload: unknown,
  ) {
    super(`${method} ${path} -> ${status}`);
    this.name = 'ApiError';
  }
}

/** Convenience: build a client from the configured baseURL + an optional auth header. */
export function makeClient(page: Page, token?: string): ApiClient {
  return new ApiClient({ baseUrl: page.url() || 'http://localhost', token });
}
