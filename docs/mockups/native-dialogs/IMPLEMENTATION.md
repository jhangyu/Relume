# Implementation spec: native dialogs → Fluent 2 lightboxes

Contract: `docs/logs/2026-10-30-native-dialogs-contract.md`. Approved mockups: this folder (`shared.css`, `apps.html`, `projects.html`, `devices.html`, `settings.html`, `schedules.html`). Reviews: `docs/logs/2026-10-30-mockup-review-style.md`, `docs/logs/2026-10-30-mockup-review-layout.md` (both "Round 2": all pass). Inventories: `docs/logs/2026-10-30-dialogs-inventory-{a,b,c}.md`.

## One shared helper (owner: impl-core), used by every confirm
Add to `local/webui/static/app.js`, next to `ask(dlg)`, and reuse `ask`:

```js
// confirmDialog({ title, body, action, destructive = true, cls = '' }) -> Promise<boolean>
// title: plain string; body: result of the html`` tag (interpolations already escaped) or plain string;
// action: verb label of the confirm button; destructive → .btn.destructive else .btn.primary;
// cls: extra class on the <dialog> (e.g. 'nested' for the stacked regex confirm).
// Builds the approved markup (h2.subtitle1, p.subtle-text.dlg-msg with aria-describedby, .dlg-actions: Cancel(autofocus when destructive) then action),
// appends it to <body>, resolves true only when the action button closed it, removes the element after close.
```
Exact markup, classes and texts: copy from the mockups. Escape and Cancel resolve false (native `<dialog>` cancel). The resolved value must not depend on `returnValue` left over from an earlier open.

## CSS (owner: impl-core)
Move the rules of `shared.css` into `local/webui/static/app.css` at the places its header comment names. Page-specific "NEW CSS" blocks go into that page's inline `<style>` (projects.html, devices.html, settings.html) or app.css if the block says so. `shared.css` itself is not served.

## Call sites
| Owner | Call site | Dialog |
|---|---|---|
| impl-core | apps.js:431 Discard unsaved changes | confirmDialog, destructive, per apps.html |
| impl-core | apps.js:565 Disable app | confirmDialog, destructive, per apps.html |
| impl-core | apps.js:143 Escape handler | return early when `document.querySelector('dialog[open]')` |
| impl-pages-a | projects.js:201 Unfollow | confirmDialog, destructive |
| impl-pages-a | projects.js:364 Replace regex (stacked over #addDlg) | confirmDialog, not destructive, cls 'nested' (420px) |
| impl-pages-a | devices.js:96 Reconnect port | static `<dialog>` in devices.html per devices.html mockup, opened with `ask()`; inline validation: whole number 1–65535, on invalid keep open and show the error state (message names expected range and the value entered) |
| impl-pages-a | devices.js:138 Remove device | confirmDialog, destructive |
| impl-pages-a | devices.js:142 Uninstall & retry | confirmDialog, destructive |
| impl-pages-b | settings.js:124 Clean up | confirmDialog, destructive, body with the facts summary per settings.html |
| impl-pages-b | settings.js:170 Revoke phone | confirmDialog, destructive, names the phone (hoist the list so the handler can find the row) |
| impl-pages-b | schedules.js:167 Delete schedule | confirmDialog, destructive |

Page implementers code against the helper signature above; impl-core lands it first and messages them when it is in `app.js`.

## Behavior that must not change
Cancel = nothing happens (same early return as today). Confirm = exactly the old code path. `closeDrawer()` keeps returning false on cancel; callers that relied on the sync boolean must be adapted to the async result (grep every caller).
