# Companion update trigger: design

Status: design only, nothing implemented. Line numbers are at HEAD `1f7d8fb`.

Goal: the companion prompts for an update only when the server's app version (`version_code` / `version_name`) or
patches version differs from what the phone has. A re-patch that keeps both must produce no install step and no prompt.

Input: `scripts/tmp/prompt-mechanism-report.md` (static trace). Its findings agree with this design, and everything
below was also checked against the code directly.

## 1. Root cause (confirmed in code)

- `local/webui/ota.py:186-187`: `plan()` returns `install` / `new build` whenever `last['build_job_id'] != a['job_id']`.
- Every build has a new job id and a new artifact sha256 (`build.py:215-223`).
- `sync/CheckWorker.kt:33-41` starts a background run for any `install` step. `BackgroundStart.kt:19-25` filters only on
  the declined map, which is keyed on sha256. A new sha256 therefore always passes.
- `ui/AppStatus.kt:82-91` (`cardAction`) already compares versions, so the card and the plan disagree today.

When a same-version rebuild happens: `needs_build` (`build.py:764-776`) skips a rebuild when `inputs_sha` is unchanged.
So a rebuild with the same versions comes from one of:

- a forced rebuild (`apps.py:245-247`, `apps.py:591`);
- a retry after a failed build (`build.py:768-771`);
- a changed input that leaves the versions alone: `cli_tag`, patch `overrides`, `engine_options`, variants, bundle
  (`apps.py:656-664`);
- in latest mode, a new newest entry in `apkmirror_versions` (`apps.py:614`) when the build still lands on the same version.

Which of these caused the reported prompts is not known: the trace was static. The fix does not depend on it. See section 8.

## 2. Chosen approach

Option (a): compare versions against the phone's install record, in the server planner. The sha256 keeps its authority
over which build the phone holds, and stops being the trigger for an update.

New rule in `plan()`, in order:

1. Package not on the phone -> `install` / `missing on phone` (unchanged).
2. No record -> `install` / `new build` (unchanged).
3. Record is another job AND any of `version_code`, `version_name`, patches version differs from the artifact ->
   `install` / `new build`.
4. Phone drifted: reported `version_code` differs from the record, or reported signer differs from the artifact signer
   -> `install` / `changed on phone`.
5. Else `up_to_date`.

Why this one:

- One rule in the shared planner covers all consumers: app sync, `/api/me/plan`, the ADB OTA job, the device view.
- An `up_to_date` step has no download, so the plan `revision` does not change on a re-patch.
- It matches `cardAction`, so the plan and the card agree.
- It works for hashless companions (up to v1.4.0) through their receipts.

### Phone holds the old sha256, versions unchanged (the decisions 14/15 question)

It is `up_to_date`. `install_records` is unchanged in its authority:

- Reported hash equals the published file -> record of the current build (`ota.py:72-74`, unchanged).
- Reported hash differs and the record names the *current* job -> record dropped (`ota.py:75-76`, unchanged): that
  phone does not hold the build it claims, and it gets `install`.
- Reported hash differs and the record names an *older* job -> the older record stays (unchanged). Before, this meant
  "older, so install". Now the planner compares that record's versions with the artifact.

The record kept is the older build's: `build_job_id` J1 and J1's versions. It stays until the phone really installs
something else. `assignments[].installed` keeps showing the build the phone holds, not the newest one.

There is no conflict with decision 15: its bug was a lost receipt causing a reinstall, and the hash still prevents that.

## 3. Traps the simple fix would hit (all in the change list)

The one-line change to `plan()` is not enough. Each of these produces a wrong prompt or a loop.

**Trap 1: ADB phones loop.** `_run_item` (`ota.py:801-802`) writes the `up_to_date` row with `a=step['artifact']`, so the
row names the NEW job while the phone holds the old file. On the next plan, `ota.py:75-76` sees "record names the
current job, hash differs", drops the record, and plans `install`. Fix: the row carries the install record, not the
artifact.

**Trap 2: a hash match is never stored.** The hash-matched record exists only in memory (`ota.py:72-74`). A phone with
no receipt (a lost receipt, or a receipt refused with 409 `stale plan`) has no record once the next re-patch changes
the published hash, and rule 2 prompts. Looking the hash up in old `artifacts` rows does not work:
`ARTIFACT_RETENTION_DEFAULT = 1` (`build.py:13`) and `delete_apks` removes the rows (`build.py:633`). Fix: `sync()`
stores the match as an `ota_installs` row while the hash still matches.

**Trap 3: the patch version comes from a purgeable table.** `last_installs` reads it through
`LEFT JOIN build_jobs` (`ota.py:46-47`). `jobs._cleanup` (`jobs.py:447-455`) deletes old build jobs that have no artifact,
and `build_jobs` cascades. After this fix a phone stays on an old build for a long time, so that job does get purged.
`patch_version` then becomes None, differs from the artifact, and prompts. Fix: store the patches version on the
`ota_installs` row.

**Trap 4: a foreign file with equal versions.** A stock APK with the same `version_code` and an older record now looks
`up_to_date`, because the hash cannot tell a foreign file from an older build of ours when the older build's row is gone.
Fix: rule 4 compares the signer. `KEYSTORE` is fixed per engine (`apps.py:12`), so a re-patch keeps the signer and this
does not bring the noise back. A same-signer foreign file is not detected: see section 7.

## 4. Rejected options

- **(b) sha256 delta only when versions differ.** Same outcome as (a) when versions differ, and when they are equal the
  sha256 adds nothing. It keeps a sha256 comparison in the planner that can never decide.
- **(c) keep `job_id`, suppress when versions equal.** This is (a) written as an exception on top of the old rule, and it
  still has all four traps.
- **Compare against the phone's report only, without a record.** The phone does not report a patches version, so a
  patch bump with the same `version_code` is invisible. That breaks a required case.
- **Treat "no record, same version and signer" as `up_to_date`.** It removes trap 2 without a write, but silently misses
  a patch bump on such a phone. A wrong extra prompt is cheaper than a missed update.
- **Hash history table, or keep `artifacts` rows after retention.** It would work, but it needs a new table and backfill,
  or touches every `artifacts` reader and blocks job cleanup (`ON DELETE RESTRICT`). Trap 2 costs about ten lines instead.
- **Companion-side version gate.** It duplicates the rule per client, and the ADB job and web `pending` would stay wrong.

## 5. Answers to the required questions

**Q2, companion change.** The fix itself is server only. One small companion change is recommended for the declined map
(next item). `CheckWorker`, `cardAction`, `atTarget` and hash reporting do not change.

**Q3, at-target check.** No conflict, no change. `VerifyRules.atTarget` (`ApkVerifier.kt:73-75`) runs only on steps with a
download (`InstallWorker.kt:72`). The comment "a patch-only rebuild keeps the version code" describes a patch bump with
the same `version_code`. That is still an `install` step under the new rule, and the hash read is still what tells the old
file from the new one. The same-versions rebuild no longer reaches this code.

**Q3, declined map.** The re-patch noise is fixed for phones at the same versions. One case is left: the phone is on patches
v1, the user declines the v2 build (sha2), and a re-patch of v2 publishes sha3. `isDeclined` compares sha256, so the user is
asked again for versions already declined (`BackgroundStart.kt:20`, `InstallRun.kt:205`). Fix: the declined value becomes a
version key instead of the sha256. The test `BackgroundStartTest.kt:126-128` ("a newer build") must bump a version.

**Q4, stored data.** Migration 023 adds one nullable column and backfills it. No row is deleted. Rows whose build job
is already purged stay NULL, which differs from the artifact, so such a phone gets one prompt and then has a full
record. No backward compatibility work is needed.

After deploy:

- A phone being prompted today that has a receipt stops being prompted.
- A phone with a hash match stores it on its next sync.
- A phone with no receipt and no hash match gets one last prompt, and has a receipt after installing.

`ota_installs` grows by one row only when a phone's hash matches a build not yet recorded, and on ADB OTA runs as it
already does.

**Effect on the web UI.** `pending` (`ota.py:255`) follows the plan, so `static/devices.js:34,41,46` shows `current` after a
re-patch. No web change.

## 6. Callers grep

Command: `grep -rnE '\b(plan|install_records|latest_artifact|_drifted)\(' local scripts android/app/src`, plus
`last_installs`, `_record(`, `item_inst`. Copies under `scripts/tmp/` are scratch and excluded.

| Symbol | Caller | Depends on old behaviour? |
|---|---|---|
| `plan()` | `ota.py:237` `_plan_for` | No code change. Feeds all rows below |
| `_plan_for` | `ota.py:245` `_device_view`, `:430` `app_plan`, `:444` `_sync_json`, `:885` `handle` | No. `handle` is affected through trap 1 |
| `plan()` | `tests/test_device_ota.py:52-123` | **Yes: `:54-55` expects `new build` for a job delta alone.** `inst()` needs `version_name`, `patch_version` |
| `install_records()` | `ota.py:236` | Logic unchanged. **`ota.py:75` is the other half of trap 1** |
| `last_installs()` | `ota.py:67` | **Yes: trap 3, source of `patch_version` changes** |
| `_drifted()` | `ota.py:188` | Signature gains the artifact |
| `_record()` | `ota.py:779,783,793,798,800,802,808,811,812` | **`:802` yes, trap 1.** Others only write the new column |
| `latest_artifact()` | `ota.py:233` planner input | No |
| `latest_artifact()` | `ota.py:622` `_receipt_target` | No. Still 409s a receipt for a non-latest artifact: section 7 |
| `latest_artifact()` | `ota.py:805` `_run_item`, `:819` `_recovery_steps` | No. Only reached by `install` steps and manual recovery |
| `latest_artifact()` | `apps.py:115` `_build_facts` | No. Card target, version based already |
| `latest_artifact()` | `tests/test_build_service.py:902-939` | No |
| `app_plan` / `me_plan` scope | `tests/test_device_app.py:192,200` | No. No record, and same job |
| `last_installs` patch source | `tests/test_device_app.py:145-151` | **Yes: asserts the join.** The patch now comes from the row |
| hash rule | `tests/test_device_app.py:155-178` | No. All cases keep their result |
| ADB hash | `tests/test_device_otajob.py:264-292` | No. Same job path |

## 7. Known limits

- A same-signer foreign file with equal versions, with an older record, reads `up_to_date`. In practice this is another
  build from this server, whose versions equal the published ones.
- Install-and-re-patch race: the phone installs J1 and J2 is published before the receipt. The receipt gets 409
  (`ota.py:623-624`), and the hash no longer matches, so one extra prompt follows. Left as is: it self-heals on the next install.
- ADB records can still go when old OTA jobs are purged (`ota_installs.job_id` cascades). Each OTA run writes a fresh
  row, so this needs more than 500 newer OTA jobs without one for this phone. Left as is.
- Patch downgrade (a channel or pin change, v2 to v1, same `version_code`): the plan says `install`, because it differs.
  `cardAction` says Installed, because it only upgrades. Same as today.

## 8. Open decision for the lead / user

**A rebuild with changed settings but unchanged versions will no longer reach phones.** The brief's rule is literal,
and this design follows it. If the user changes a patch option, or ticks "rebuild" to get a changed result, and the app and
patches versions stay the same, the phone stays on the old build with no prompt and no install button.

- Default in this design: accept it (the stated requirement).
- Alternative: also store the `base` part of `inputs_sha` on `ota_installs` and count it as a change. Option and bundle
  changes then reach phones. It needs excluding `cli_tag` and the fingerprint `version` from that hash, or a CLI update
  or a new APKMirror listing would prompt again, so it is more than a one-line addition.

## 9. Change list

All lines at `1f7d8fb`.

| File:line | Change |
|---|---|
| `local/webui/schema/023_ota_installs_patches_version.sql` (new) | `ALTER TABLE ota_installs ADD COLUMN patches_version TEXT;` then `UPDATE` from `build_jobs` where `build_job_id` matches |
| `ota.py:41-55` `last_installs` | Read `o.patches_version`, drop the `build_jobs` join, update the docstring |
| `ota.py:149-152` `_drifted` | Add `a`. Also true when the report has a signer, the artifact has one, and they differ. ADB reports None: skipped (`devices.py:53`) |
| `ota.py:149` (above it) | New `_new_versions(last, a)`, see below |
| `ota.py:186` | `elif _new_versions(last, a):` |
| `ota.py:188` | `elif _drifted(present, pkg, last, a):` |
| `ota.py:195` | `steps.append(dict(step, action=action, reason=reason, installed=last))`. `_item_step` copies named keys, so the wire format and `revision` do not change |
| `ota.py:441-446` `_sync_json` | After `_plan_for`, unless `devices.is_legacy(dev)`, call `_remember_hash_matches(dev['id'], v, reported)` |
| `ota.py:441` (above it) | New `_remember_hash_matches`, see below |
| `ota.py:607-634` `_receipt_target` | Return the patches version too (`a['patches_version']` / None) |
| `ota.py:652-658` `post_install` | Write `patches_version` |
| `ota.py:714-723` `_record` | Write `patches_version` from `a` |
| `ota.py:801-802` `_run_item` | `up_to_date` writes the step's `installed` record: job, names and patch of what the phone holds |
| `InstallRun.kt:101,193-199,205` | Value becomes `declineKey(d)`, comments updated |
| `InstallRun.kt` (new, top level) | `fun declineKey(d: Download) = listOf(d.versionCode, d.versionName, d.patchVersion, d.tag).joinToString("\|")` |
| `BackgroundStart.kt:13,20` | `declined[...] == declineKey(s.download)`, comment updated |

```python
def _new_versions(last, a):
    """No record, or a record of another build with another app or patches version. A rebuild that keeps all three
    is not new for the phone: its file differs, what it runs does not."""
    return last is None or (last['build_job_id'] != a['job_id'] and
                            (last['version_code'], last['version_name'], last['patch_version'])
                            != (a['version_code'], a['version_name'], a['patches_version']))


def _remember_hash_matches(device_id, v, present):
    """Stores a hash match as a history row: the published file changes at the next re-patch, and retention deletes the
    artifact row, so this sync is the last chance to know which build the phone holds."""
    # for each (iid, a) in v['arts']: a and a['package'], reported hash == a['sha256'],
    # and last_installs(device_id)[0].get(iid, {}).get('build_job_id') != a['job_id']
    # -> INSERT ota_installs, job_id NULL, kind 'item', result 'up_to_date', the artifact's job, names and patch version
```

Idempotent: after the insert, the newest row names that job, so the next sync writes nothing.

## 10. Work packages

| Package | Owned files | Scope |
|---|---|---|
| WP1 server planner | `local/webui/ota.py`, `local/webui/schema/023_ota_installs_patches_version.sql` | Rule, three traps, patch column |
| WP2 python tests | `local/webui/tests/test_device_ota.py`, `test_device_app.py`, `test_device_otajob.py`, `device_helpers.py` | Section 11 cases, update `:54-55` and `inst()` |
| WP3 companion declined key | `InstallRun.kt`, `BackgroundStart.kt`, `android/app/src/test/.../sync/BackgroundStartTest.kt` | Decline by versions, not sha256 |
| WP4 docs | `docs/superpowers/specs/2026-10-25-android-companion-design.md`, `README.md` (each only where it states the `new build` rule) | State the new rule |

WP1 and WP2 are one contract: WP2 can write tests in parallel, and its gate goes green only with WP1. WP3 ships with the next
companion release and does not block the server. WP1 alone stops the reported prompts.

## 11. Test cases (python leg)

`ota.plan`, pure (`test_device_ota.py`):

1. Same versions + new sha (job 6, record job 5, all three facts equal) -> `up_to_date`.
2. `version_code` bump -> `install` / `new build`.
3. `version_name` change, same code -> `install` / `new build`.
4. `patches_version` bump, same code and name -> `install` / `new build`.
5. Same versions, phone reports a higher `version_code` than the record -> `install` / `changed on phone`.
6. Same versions, reported signer differs from the artifact -> `install` / `changed on phone`. Signer None -> `up_to_date`.

Through sync (`test_device_app.py`):

7. Hash reported equals the new sha, record is older with other versions -> `up_to_date`, record names the new build.
8. Trap 2: no receipt, hash matches J1, then J2 is published with the same versions and J1's artifact row is deleted ->
   `up_to_date`. A second sync adds no row.
9. Trap 2 negative: a legacy row or a hashless report writes no row.
10. Trap 3: receipt for J1, then the `build_jobs` row is deleted -> `patch_version` kept, `up_to_date`.
11. Existing: the receipt names the current build, the file differs -> `install` (keep `test_hash_decides_needs_update`).

ADB job (`test_device_otajob.py`):

12. Trap 1: phone at J1, J2 published with the same versions, two OTA jobs -> no `adb install`, the `up_to_date` row names J1
    both times.

Gate: `python3 scripts/test.py --legs python`

Before commit, per project rules: `python3 scripts/test.py`, the Linux gate from `CLAUDE.md`, and for WP3
`ANDROID_HOME=<sdk> python3 scripts/test.py --legs android`.
