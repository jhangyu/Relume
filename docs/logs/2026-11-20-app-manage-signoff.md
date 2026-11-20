# App Manage — sign-off

The contract `docs/logs/2026-11-20-app-manage-contract.md` (as amended) was met on main at `ac88dbb`, inside the 2-round budget. Live device checks are pending on the user: `docs/logs/2026-11-20-app-manage-live-checks.md`.

## Commits
| Commit | Content |
|---|---|
| 8854f15, 63c5fc1, eddf514, ac88dbb | Design, preflight, Play probe, plan, contract amendment, round 2 brief, locale probe |
| 679dff0 | Matrix logic, ADB inventory reader, `adb_pipe`, `install_ref`, Play URL resolution |
| 0fcd9c9 | App Manage page and sidebar entry |
| f590e97 | Companion inventory report, usage-access row, 15-minute interval |
| f3dff8f | Migration 024, store, shared `assign_in`/`unassign_in` |
| 432aab9 | Sync ingest and action pickup, locale store, receipt finish, adopt, migration 025 |
| f0ab587 | Web and companion routes, SSE topic, ADB job kind |
| 0f2a2c6 | Locale passed to Play, phone locale report, actions kept from both sync responses |
| 686f034 | Companion action executor after every sync |

## Gate evidence (R2-E, HEAD ac88dbb, clean `local`/`android`)
| Check | Command | RC | Decisive output |
|---|---|---|---|
| Full suite | `ANDROID_HOME=… python3 scripts/test.py` | 0 | 699 tests, 8 skipped (the 8 Linux/root-only tests named in acceptance 8), wall 9.0 s, slowest 0.397 s |
| Linux gate | Command from `CLAUDE.md` | 0 | 622 tests, 0 skipped, 59 s |
| CI local | `python3 scripts/ci_local.py` | 0 | `ci_local: all CI steps passed` |
| Acceptance 1–7, 11, 12, 14 | Unittest, grep and `node --check` commands as written in the contract | 0 | `token=` grep has no match (RC 1, the expected result). ActionRulesTest 8 and InventoryDiffTest 4 ran. |

Every new core test was shown red once, then green, by its implementer.

## Parking lot (for the user)
1. **Cross-IP Play URLs:** not proven yet; live check 8 settles it. If the URLs turn out to be IP-bound, the user's fallback is an in-app Play client on the companion, as a follow-up.
2. **`dumpsys diskstats` parser:** it follows the AOSP format and has not been checked against a real device; live check 3 settles it.
3. **Server switch:** a companion's saved inventory snapshot is not tied to one server. Switching servers could produce a wrong diff when the revision numbers happen to match.
4. **Unknown action kind:** if the server ever sent one, the companion would fail to decode the whole sync response and lose that response's actions. Today the server sends only the known kinds.
5. **Uninstall prompt:** with notifications off and the app in the background, the uninstall prompt waits until a later app start.
6. **Build action vs. queued plan:** a build action started by the executor replaces a plan the user had queued in the app UI. This is the existing InstallRunner rule.
7. **Known limits kept by design:**
   - ADB-only apps have no label or icon.
   - ADB actions run one at a time.
   - Play splits use the profile's density (`xxhdpi`) and the phone's first locale only.
