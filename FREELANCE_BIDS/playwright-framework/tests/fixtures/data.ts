import { test as base } from '@playwright/test';

/**
 * Test data — the "fixture" payload.
 *
 * Keeping data in one typed module is the point of the fixture-based approach:
 *  - every test pulls from `userData`, `orderData`, etc., so values are consistent
 *    and reviewed in one place (not scattered string literals across specs);
 *  - a test can override a single field without touching the rest.
 */
export interface User {
  email: string;
  password: string;
  name: string;
  /** optional, for role-permission tests */
  role?: 'admin' | 'member';
}

export const users = {
  admin: { email: 'admin@example.com', password: 'S3cure!Pass', name: 'Admin', role: 'admin' },
  member: { email: 'member@example.com', password: 'Passw0rd', name: 'Member', role: 'member' },
} satisfies Record<string, User>;

export const order = {
  sku: 'SKU-1001',
  quantity: 2,
  expectedTotal: 49.98,
} as const;
