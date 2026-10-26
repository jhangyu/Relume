# Polish batch — frozen contract

Spec: `docs/superpowers/specs/2026-10-26-polish-batch-design.md`. Only the user can change this contract.

**End state:** the 9 fixes in the spec are committed on `main`, and the debug APK built from that HEAD is installed on phone `10AFCF269C007FB`.

**In scope:** spec requirements 1–9, Android debug rebuild and install.

**Out of scope:** push, `companion-v*` tag, companion release, the earlier marketplace parking lot, final UI review (the user does it), test runner.

**Acceptance:**
1. Apps API returns Followed-project apps with a source marker; web and Android Apps lists show them with only Download and Install actions.
2. Followed projects "Install to phone" and "Check now" use the shared button classes; padding is fixed (Android: Material3 components).
3. The pairing-pending root cause is written in the report and fixed at the cause.
4. Every icon in the web sprite comes from Fluent UI System Icons; no hand-drawn inline SVG icons remain in pages; no new package or build step.
5. Apps search box and its buttons, Marketplace buttons and selects, Schedules Repeat and At use shared `app.css` classes; per-page hand-made control styles are deleted.
6. Android Settings has a Server URL editor that checks `/api/health` before saving, and on success clears login and returns to the login screen.
6b. Marketplace (web and Android) loads the next page automatically on scroll; no "Load more" button remains.
7. No new tests. UI-type tests in changed areas are deleted. Linux gate (python, shell) and Android leg are green.
8. Backend reviewer verdict: merge-ready.
9. Debug APK from final HEAD: `adb install` reports Success on `10AFCF269C007FB`.

**Round budget:** 2 implementation rounds.

## Parking lot
- Two tests sit near the 1 s cap under parallel load: test_apps needs_build_matrix (1.169 s) and test_device_otajob auto_push_target_runs_project_only (1.027 s) in the first Linux gate run of round 1; rerun green.
- Reviewer nit: ota.py:674 re-reads the phone even when no install ran; a cancelled job or unreachable phone can wait up to 60 s more.
- Reviewer nit: projects.py:272-273 a project deleted between two queries makes GET /api/apps return 500.
- Pre-existing: Followed projects have no versionCode drift check (ota.py:85-95).
- Pre-existing: a device token can call POST /api/projects/{id}/install.

## Amendment (user, after device test)
- Followed apps in Apps (web and Android) show only Install; the Download action is removed (code deleted, not hidden).
- Followed apps show their real app icon. Root cause of the missing icon is found and fixed at the cause.
- Acceptance: grep finds no Download action for followed apps in apps.js and the Android Apps code; Linux gate and Android leg green; backend reviewer merge-ready for any backend change; debug APK from final HEAD installed on 10AFCF269C007FB.
