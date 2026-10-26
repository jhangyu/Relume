# Marketplace refactor — decisions after the requirements doc

These decisions are binding, made by the user or by the lead with the user's acknowledgement on 2026-10-26. They add to `docs/superpowers/specs/2026-10-26-marketplace-requirements.md`, and they override that doc where the two conflict.

## Catalog and sources
- The 7 GitLab bundles are listed in Marketplace but cannot be selected. Their reason reads "GitLab releases not supported".
- A custom repo without `patches-list.json` is refused with a clear message.
- A malformed bundle entry in bundles.json is skipped. The error goes into the catalog feed status, which the UI shows, and the rest of the catalog still updates. This overrides the "reject the whole file" rule in the catalog part. A file that does not parse at all, or whose top-level shape is wrong, is still rejected whole, and the last good copy stays in use.
- The catalog bundle with `branch: dev` maps to the GitHub prerelease channel. This is accepted as an assumption.

## APKMirror locate
- When two or more app pages match the package, the user picks one. There is no heuristic.
- Requests to apkmirror.com are spaced 10 s apart.
- An app can be enabled without a download URL. Locate runs inside the update job. When it finds nothing, or the result is ambiguous, the Apps tab shows "needs URL" or "pick one" with a paste-URL or pick action.

## App icons (amend catalog part section 5, see `docs/logs/2026-10-26-app-icon-check.md`)
- The Play page fetch gets its own size cap of 4 MiB. The 256 KiB cap stays for images.
- Rewrite the Play `og:image` size suffix to `=s256-rw` before fetching.
- `upstream.http_get` must return the final URL after redirects, so the host allowlist check sees it.
- State in the spec that the first icon fetch can take up to about 20 s.
- The 24 apps without a usable icon show a visible letter tile. The server logs the failure and records it. `img.samsungapps.com` is not added to the allowlist.
- Android needs no image-library change, because `AppTile` already loads authenticated icons.

## UI
- Chosen design: candidate 2 "Storefront", `docs/superpowers/mockups/marketplace/candidate-2/`.
  - The web half is used AS IS, at commit 0d55fc3 (`web/`, root `icons.js`, root `tokens.md`).
  - The Android half is the revised version in `android/`, with official Material Symbols Rounded icons in `android/icons-material.js` and tokens in `android/tokens.md`.
  - The candidate-1 and candidate-3 folders are references only.
- Light theme only on both platforms. Remove any existing dark-mode code.
- Android:
  - No dynamic colour at all. The app uses one fixed palette on every API level, so `dynamicLightColorScheme` is never used.
  - Surfaces are neutral grey with no hue. Soft multicolour accents are assigned by meaning:
    - violet = build on the server
    - sky blue = choose and edit
    - coral = send to the phone
    - each bottom-nav tab gets its own indicator hue
    - status and category tags use pastel hues
  - The type scale is one MD3 step smaller than the default.
  - Official Material Symbols only.
- Android Apps tab: each app has an Install button that pulls the server-built APK and hands it to the system package installer. The states are not installed, update, installing, and installed with its version. Android has no Download button.
- Web Apps tab keeps Download, because browsers cannot install APKs.
- Android server and account settings sit behind the account avatar in the top bar. Android has three tabs: Marketplace, Apps, Schedules.

## Process
- Two implementation rounds with 4–5 implementers each.
- A reviewer runs in the last round only.
- No test-runner member.
