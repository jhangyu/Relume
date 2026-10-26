# Polish batch — frozen contract

Spec: `docs/superpowers/specs/2026-10-26-polish-batch-design.md`. Only the user can change this contract.

**End state:** the 8 fixes in the spec are committed on `main`, and the debug APK built from that HEAD is installed on phone `10AFCF269C007FB`.

**In scope:** spec requirements 1–8, Android debug rebuild and install.

**Out of scope:** push, `companion-v*` tag, companion release, the earlier marketplace parking lot, final UI review (the user does it), test runner.

**Acceptance:**
1. Apps API returns Followed-project apps with a source marker; web and Android Apps lists show them with only Download and Install actions.
2. Followed projects "Install to phone" and "Check now" use the shared button classes; padding is fixed (Android: Material3 components).
3. The pairing-pending root cause is written in the report and fixed at the cause.
4. Every icon in the web sprite comes from Fluent UI System Icons; no hand-drawn inline SVG icons remain in pages; no new package or build step.
5. Apps search box and its buttons, Marketplace buttons and selects, Schedules Repeat and At use shared `app.css` classes; per-page hand-made control styles are deleted.
6. Android Settings has a Server URL editor that checks `/api/health` before saving, and on success clears login and returns to the login screen.
7. No new tests. UI-type tests in changed areas are deleted. Linux gate (python, shell) and Android leg are green.
8. Backend reviewer verdict: merge-ready.
9. Debug APK from final HEAD: `adb install` reports Success on `10AFCF269C007FB`.

**Round budget:** 2 implementation rounds.

## Parking lot
