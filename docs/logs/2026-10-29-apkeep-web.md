# apkeep web UI (contract item 2, A6/A7)

Changes: local/webui/static/settings.html (Google Play card), settings.js (renderSettings gp block, saveGooglePlay), apps.js (SOURCES, renderUrls source dropdown, onEdit f=src).

Checked by reading request bodies against the frozen API (no dev server run, no UI tests committed):
- Save: `PUT /api/settings {"google_play": {"email": str, "aas_token": str}}` (both required client side). Remove: `{"google_play": null}`. Token input cleared after save, never rendered back. GET `google_play {set, email}` drives badge and email field.
- Source dropdown: sets every variant `download.kind` (apkmirror/apkpure/googleplay) with `url: null`, sent via existing `PUT /api/apps/<id>` `variants` (settingsBody compares variants). URL option only when a variant already has kind url, restoring its original download object.
- APKMirror field shown only for apkmirror, APKPure field for apkmirror (optional) and apkpure, none for googleplay/url. Google Play hint: latest only, credentials in Settings.
- `node --check` apps.js, settings.js RC=0. `python3 scripts/test.py --legs python`: 374 tests, 6 skipped, no static checks cover these files.
- Not verified live: needs the backend (apkeep-server) to accept kind googleplay (catalog.py:765 still rejects it until that lands).
