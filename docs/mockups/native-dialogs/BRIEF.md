# Design brief: native browser dialogs → Fluent 2 lightboxes

Contract (read first): `docs/logs/2026-10-30-native-dialogs-contract.md`.
Inventories (call sites, exact messages, triggers, existing dialogs): `docs/logs/2026-10-30-dialogs-inventory-a.md`, `-b.md`, `-c.md`.

## Ground truth for style
- The web has no UI SDK. `local/webui/static/app.css` is the hand-ported Fluent 2 stylesheet (tokens, `.btn`, `.btn.primary`, `dialog`, `.dlg-actions`, `.subtitle1`, `.subtle-text`, `.field`, `.input`, `.hint`). Reuse its tokens and classes; never hard-code colors or radii that a token already covers.
- The "lightbox SDK" is `<dialog>` + `ask(dlg)` in `local/webui/static/app.js` (returns the clicked button's `value`).
- Reference confirm dialog: `local/webui/static/index.html` `#dlg-del` (lines ~112-115).
- Page-local dialog classes exist and differ per page: projects.html uses `.dlg-h/.dlg-b/.dlg-f` (inline `<style>` in that page); devices.html uses `.card-head/.card-pad/.dlg-actions`. New dialogs on a page must look consistent with that page's existing dialogs AND with every other new dialog.

## Shared rules (all designers)
1. One mockup HTML file per page group in `docs/mockups/native-dialogs/` (file names given in your assignment). Each file links the real stylesheet: `<link rel="stylesheet" href="../../../local/webui/static/app.css">`, and `shared.css` from this folder. Any CSS that does not exist in app.css goes in the mockup's own `<style>` block under the comment `/* NEW CSS — to be added to <target file> */`, so implementers can copy it verbatim.
2. Each mockup shows every dialog of its group open (use `<dialog open>` or render each inside a static frame with the backdrop), with real message text from the inventory, over a faded copy of the real page behind it. Show each dialog at desktop width AND at 390 px mobile width (e.g. side-by-side frames or an `iframe`-free narrow container).
3. Confirmation layout (owned and defined by designer-shared in `shared.css`; others use these class names and must not redefine them):
   - title `h2.subtitle1`, body `p.subtle-text`, actions row `.dlg-actions` right-aligned, Cancel first (secondary `.btn`), action second.
   - destructive action uses `.btn.danger` (defined in `shared.css`); non-destructive action uses `.btn.primary`.
   - optional leading icon slot from the existing sprite (`<svg class="i"><use href="#i-..."/></svg>`); if the sprite symbol is not in app.js, include the official Fluent UI System Icons 20px regular path in the mockup and say which symbol to add.
   - action button label is the verb of the action ("Delete", "Unfollow", "Remove", "Discard"), never "OK".
   - user-provided names inside the message are shown in a `<strong>` and must be HTML-escaped in implementation (note this in a comment).
4. Layout quality bar (reviewers check these): nothing off-center, no unintended left/right alignment, consistent 24px dialog padding (app.css dialog rule), consistent gaps, button heights equal, long names wrap without overflow, mobile: actions stay on one row or stack full-width consistently, no horizontal scroll.
5. Keyboard: Escape and the Cancel button both cancel; initial focus on Cancel for destructive dialogs. Note it in a comment.
6. Use the skills ui-ux-pro-max:ui-ux-pro-max, ui-ux-pro-max:design, ui-ux-pro-max:ui-styling and frontend-design:frontend-design for guidance, but the existing Fluent 2 look of the app wins over any skill suggestion. Do not introduce fonts, frameworks, Tailwind or CDN links.
