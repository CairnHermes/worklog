# Playwright + TypeScript Automation Framework

A robust, scalable UI + API automation framework built to the spec:
**Playwright + TypeScript, Page Object Model + fixture-based data, UI and API
automation in one repo, CI-ready.**

## What's included

```
playwright.config.ts          # one source of truth: projects, sharding, retries, reporters
tsconfig.json                 # strict TS
tests/
  fixtures/
    index.ts                  # test.extend: injects page objects + auth page per test
    data.ts                   # typed test data (users, orders) in ONE reviewed place
    auth.setup.ts             # (optional) persist login state -> storageState
  pages/
    BasePage.ts               # abstract base: goto, safeClick, safeFill, expectVisible
    LoginPage.ts              # concrete Page Object (locators private, intent methods public)
  api/
    ApiClient.ts              # typed fetch wrapper: GET/POST/PUT/DELETE + ApiError
    health.api.spec.ts        # pure API spec (browser-free, runs in the `api` project)
  login.spec.ts               # UI spec written against fixtures, not selectors
```

## How the design maps to the requirements

| Requirement | Where |
|---|---|
| Page Object Model | `BasePage.ts` (plumbing) + `LoginPage.ts` (behavior). Locators are private; tests call intent methods only. |
| Fixture-based | `tests/fixtures/index.ts` uses Playwright's `test.extend` DI: every test receives ready-made page objects + an auth page; data lives in `data.ts`. |
| UI + API in one framework | UI specs in `tests/*.spec.ts`; API specs in `tests/api/*.api.spec.ts`, both run under one `playwright test`. |
| Robust | Locator-first (no `waitForTimeout`), `trace: on-first-retry`, screenshot/video on failure, per-test isolated context. |
| Scalable / CI | `fullyParallel`, `workers`, `--shard=i/n`, `forbidOnly` on CI, 2 retries on CI, JUnit + HTML + list reporters. |

## Quick start

```bash
npm install
npx playwright install          # one-time browser download
npm test                         # run the whole suite
npm test -- --project=api       # browser-free API gate (fastest CI signal)
npm run test:headed             # watch the UI tests run
npm run test:report             # open the HTML report
```

## Pointing it at your app

1. Set `BASE_URL` (or change `use.baseURL` in the config) to your app.
2. Replace the `data-testid` values in `LoginPage.ts` with your app's real
   `data-testid`s (the `getByRole` fallbacks already work for buttons/links).
3. Fill `tests/fixtures/data.ts` with your real users/orders.
4. (Optional) add `tests/fixtures/auth.setup.ts` to persist a logged-in
   `storageState` so UI tests skip re-login.

That's the whole integration surface: **one page object + one data file.**

## Why this is stable

- **No arbitrary sleeps.** Every wait is an assertion or a locator state
  check (`waitFor({state:'visible'})`, `scrollIntoViewIfNeeded`), so tests are
  as fast as the app and don't flake on slow machines.
- **Locator strategy is centralized.** A selector change is a one-line edit in
  one page object, not a search across a dozen specs.
- **Data is centralized and typed.** A bad email literal can't hide in a spec;
  it's one reviewed constant.
- **UI and API share one runner and one report**, so a CI pipeline gets a
  single command and one artifact.
