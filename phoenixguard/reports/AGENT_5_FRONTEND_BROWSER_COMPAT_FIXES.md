# Agent 5 Frontend Browser Compat Fixes

CLEAR ANSWER

CSS compatibility warnings in the window tracker dashboard were addressed, and web typecheck/build passed.

CONFIDENCE LEVEL

0.84

KEY CAVEATS

`npm --prefix web run test:smoke` still fails on existing `/app` smoke assertions for missing onboarding/status labels.

FILES STUDIED

`phoenixguard/mobile_api/static/window_tracker_dashboard.html`, `web/package.json`, `web/tests/smoke.mjs`.

ERRORS FOUND

Prefix order warnings for `backdrop-filter`, `background-clip`, `appearance`, missing `-webkit-user-select`, and `-webkit-user-drag` compatibility warning.

FIXES APPLIED

Added or reordered WebKit-prefixed properties before standard properties and removed unsupported `-webkit-user-drag` usage.

TESTS RUN

`npm --prefix web run typecheck` passed. `npm --prefix web run build` passed. `npm --prefix web run test:smoke` failed on `/app` missing registration/email/license/broker/disclosure/device/tracker markers.

REMAINING RISKS

Smoke test failure remains and should be fixed in the web app or smoke expectations.
