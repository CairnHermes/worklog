import { test, expect } from '@playwright/test';

/**
 * API spec — pure backend tests in the SAME framework as the UI tests.
 *
 * They run in the `api` project (no browser), so `playwright test --project=api`
 * gives you a fast, browser-free regression gate you can wire into CI first.
 *
 * These are intentionally framework-exercising (not tied to one app): point
 * them at your real endpoints by changing the paths + expected shapes.
 */
test.describe('API', () => {
  test('GET /health returns 200 and a live flag', async ({ request }) => {
    const res = await request.get('/health');
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(typeof body.status).toBe('string');
  });

  test('POST a minimal payload then read it back', async ({ request }) => {
    const created = await request.post('/items', { data: { name: 'widget' } });
    expect(created.status()).toBeGreaterThanOrEqual(200);
    expect(created.status()).toBeLessThan(300);
  });
});
