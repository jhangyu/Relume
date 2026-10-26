-- Marketplace (spec 2026-10-26-marketplace-design §4, apps part §7): catalog tables, built-in bundle, item re-keyed
-- onto a bundle (ids kept), sources and groups dropped, one global update schedule. Foreign keys are off, so every
-- orphan delete is explicit; every match is by content, never by a fixed id.
PRAGMA foreign_keys=OFF;
BEGIN;

-- 1. catalog storage (catalog part §2)
CREATE TABLE bundle (
  id INTEGER PRIMARY KEY,
  origin TEXT NOT NULL CHECK (origin IN ('official','builtin','custom')),
  host TEXT NOT NULL CHECK (host IN ('github','gitlab')),
  repo TEXT NOT NULL,
  branch TEXT,
  engine TEXT NOT NULL DEFAULT 'morphe' CHECK (engine IN ('morphe','lspatch','npatch')),
  name TEXT NOT NULL, author TEXT, description TEXT, stars INTEGER, updated_at TEXT,
  asset_regex TEXT,                -- custom Xposed modules only; NULL = upstream.DEFAULT_REGEX
  listed INTEGER NOT NULL DEFAULT 1,
  upstream_id INTEGER REFERENCES upstream(id),   -- release tracker; created on first enable (catalog) or on add (builtin/custom)
  list_tag TEXT,                   -- builtin/custom: release tag whose patches-list.json fills bundle_patch
  list_error TEXT,                 -- builtin/custom: last list refresh failure
  CHECK (engine = 'morphe' OR (origin = 'custom' AND asset_regex IS NOT NULL)));   -- modules: custom only, regex required
CREATE UNIQUE INDEX bundle_key ON bundle(host, repo, ifnull(branch, ''));

CREATE TABLE bundle_app (          -- "bundle B targets package P"; drives the Marketplace
  bundle_id INTEGER NOT NULL REFERENCES bundle(id) ON DELETE CASCADE,
  package TEXT NOT NULL,
  versions TEXT NOT NULL,          -- JSON array newest first; [] = no version list
  PRIMARY KEY (bundle_id, package));
CREATE INDEX bundle_app_pkg ON bundle_app(package);

CREATE TABLE bundle_patch (        -- browsing list; the build uses bundle_info
  bundle_id INTEGER NOT NULL REFERENCES bundle(id) ON DELETE CASCADE,
  package TEXT NOT NULL,           -- '*' = universal
  name TEXT NOT NULL,
  description TEXT,
  default_enabled INTEGER,         -- NULL = the source did not say
  PRIMARY KEY (bundle_id, package, name));

CREATE TABLE store_app (           -- per-package metadata, one row per package any source targets
  package TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT,                   -- NULL = not in a catalog store (built-in/custom only)
  description TEXT,
  icon_url TEXT,
  icon_file TEXT, icon_tried_url TEXT);

CREATE TABLE catalog_feed (
  feed TEXT PRIMARY KEY, etag TEXT, last_attempt_at TEXT, last_ok_at TEXT, last_error TEXT,
  duplicates TEXT,                 -- JSON list
  skipped TEXT);                   -- JSON list of bundles/store entries skipped by the last parse

-- built-in bundle on the MorpheApp/morphe-patches stable upstream (created here on a fresh database)
INSERT OR IGNORE INTO upstream(kind, repo, channel) VALUES ('patches', 'MorpheApp/morphe-patches', 'stable');
INSERT INTO bundle(origin, host, repo, engine, name, upstream_id)
  SELECT 'builtin', 'github', repo, 'morphe', 'Morphe Patches', id FROM upstream
  WHERE repo = 'MorpheApp/morphe-patches' AND channel = 'stable' AND tag IS NULL;

-- 2. app: locator state (API part §1.4); names and icons move to store_app
ALTER TABLE app ADD COLUMN apkm_state TEXT NOT NULL DEFAULT 'pending'
  CHECK (apkm_state IN ('pending','found','manual','not_found','ambiguous','blocked','failed'));
ALTER TABLE app ADD COLUMN apkm_candidates TEXT;   -- JSON list (ambiguous)
ALTER TABLE app ADD COLUMN apkm_error TEXT;        -- last lookup or version-check error
ALTER TABLE app ADD COLUMN apkm_checked_at TEXT;
ALTER TABLE app ADD COLUMN apkm_retry_at TEXT;
UPDATE app SET apkm_state = 'manual' WHERE apkmirror_list_url IS NOT NULL OR apkpure_url IS NOT NULL;
ALTER TABLE app DROP COLUMN display_name;
ALTER TABLE app DROP COLUMN icon_file;
ALTER TABLE app DROP COLUMN icon_fetched_at;

-- 3. item = one enabled app per package on one bundle; only items of the built-in source survive (lowest id per package)
CREATE TABLE item_new (
  id INTEGER PRIMARY KEY,
  package TEXT NOT NULL UNIQUE REFERENCES app(package),
  bundle_id INTEGER NOT NULL REFERENCES bundle(id),
  display_name TEXT,                                   -- NULL = app name
  enabled INTEGER NOT NULL DEFAULT 1,
  version_mode TEXT NOT NULL DEFAULT 'auto' CHECK (version_mode IN ('auto','pinned','latest')),
  version_pin TEXT CHECK ((version_mode = 'pinned') = (version_pin IS NOT NULL)),
  allow_nearest INTEGER NOT NULL DEFAULT 0,
  variants TEXT NOT NULL,                              -- JSON
  patch_overrides TEXT NOT NULL DEFAULT '{}',          -- JSON {patch: {enabled?, options?}}
  engine_options TEXT);                                -- JSON {sigbypass, injectdex} for module engines, else NULL
INSERT INTO item_new(id, package, bundle_id, display_name, enabled, version_mode, version_pin, allow_nearest, variants,
                     patch_overrides, engine_options)
  SELECT i.id, i.package, b.id, i.display_name, i.enabled, i.version_mode,
         CASE WHEN i.version_mode = 'pinned' THEN i.version_pin END, i.allow_nearest, i.variants, i.patch_overrides,
         i.engine_options
  FROM item i JOIN source s ON s.id = i.source_id
  JOIN bundle b ON b.upstream_id = s.upstream_id AND b.origin = 'builtin'
  WHERE i.id = (SELECT min(i2.id) FROM item i2 JOIN source s2 ON s2.id = i2.source_id
                WHERE s2.upstream_id = s.upstream_id AND i2.package = i.package);

-- 4. rows of dropped items (artifacts before their jobs: artifacts.job_id is RESTRICT)
DELETE FROM item_dependency WHERE item_id NOT IN (SELECT id FROM item_new);
DELETE FROM device_assignments WHERE item_id NOT IN (SELECT id FROM item_new);
DELETE FROM ota_installs WHERE kind = 'item' AND ref_id NOT IN (SELECT id FROM item_new);
DELETE FROM artifacts WHERE item_id NOT IN (SELECT id FROM item_new);
DELETE FROM build_jobs WHERE item_id NOT IN (SELECT id FROM item_new);
DELETE FROM jobs WHERE kind = 'build' AND json_extract(target, '$.item_id') NOT IN (SELECT id FROM item_new);
DROP TABLE item;
ALTER TABLE item_new RENAME TO item;
DROP TABLE source;
DROP TABLE grp;
ALTER TABLE build_jobs ADD COLUMN inputs_sha TEXT;   -- fingerprint of the build inputs; NULL for builds before 010

-- 5. schedules: ota rows kept, the lowest-id app row with options.update becomes the one global update row
CREATE TABLE schedules_new (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('update','ota')),
  target TEXT NOT NULL,
  spec TEXT NOT NULL,
  options TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run_at TEXT, next_run_at TEXT);
INSERT INTO schedules_new(id, name, kind, target, spec, options, enabled, last_run_at, next_run_at)
  SELECT id, name, kind, target, spec, options, enabled, last_run_at, next_run_at FROM schedules WHERE kind = 'ota';
INSERT INTO schedules_new(id, name, kind, target, spec, options, enabled, last_run_at, next_run_at)
  SELECT id, name, 'update', '{}', spec,
         json_object('notify', json(CASE WHEN json_extract(options, '$.notify') = 0 THEN 'false' ELSE 'true' END)),
         enabled, last_run_at, next_run_at
  FROM schedules WHERE kind = 'app' AND json_extract(options, '$.update') = 1 ORDER BY id LIMIT 1;
DROP TABLE schedules;
ALTER TABLE schedules_new RENAME TO schedules;
CREATE UNIQUE INDEX schedules_one_update ON schedules(kind) WHERE kind = 'update';
UPDATE jobs SET schedule_id = NULL WHERE schedule_id NOT IN (SELECT id FROM schedules);

-- 6. jobs: kind appcheck removed (pattern 006)
DELETE FROM jobs WHERE kind = 'appcheck';
CREATE TABLE jobs_new (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('build','upstream','ota')),
  status TEXT NOT NULL CHECK (status IN ('queued','running','success','failed','cancelled')),
  trigger TEXT NOT NULL CHECK (trigger IN ('manual','schedule','upstream','after_build')),
  schedule_id INTEGER REFERENCES schedules(id) ON DELETE SET NULL,
  target TEXT NOT NULL,
  options TEXT NOT NULL DEFAULT '{}',
  reason TEXT,
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT);
INSERT INTO jobs_new(id, kind, status, trigger, schedule_id, target, options, reason, created_at, started_at, finished_at)
  SELECT id, kind, status, trigger, schedule_id, target, options, reason, created_at, started_at, finished_at FROM jobs;
DROP TABLE jobs;
ALTER TABLE jobs_new RENAME TO jobs;
CREATE INDEX jobs_kind_id ON jobs(kind, id);

-- 7. upstream rows no bundle, project or engine CLI (catalog.ENGINE_CLI) uses, and stale bundle_info
DELETE FROM upstream WHERE id NOT IN (SELECT upstream_id FROM bundle WHERE upstream_id IS NOT NULL)
  AND id NOT IN (SELECT upstream_id FROM project)
  AND NOT (repo = 'MorpheApp/morphe-desktop' AND channel = 'stable' AND tag IS NULL)
  AND NOT (repo = 'JingMatrix/LSPatch' AND channel = 'stable' AND tag IS NULL)
  AND NOT (repo = '7723mod/NPatch' AND channel = 'tag' AND tag = 'v1.0.5');
DELETE FROM bundle_info WHERE upstream_id NOT IN (SELECT id FROM upstream);
DELETE FROM bundle_info WHERE tag IS NOT (SELECT downloaded_tag FROM upstream u WHERE u.id = bundle_info.upstream_id);

-- 8. download URLs of the former catalog seed (rows that already exist are untouched)
INSERT OR IGNORE INTO app(package, apkmirror_list_url, apkmirror_example_url, apkmirror_example_url2, apkpure_url, apkm_state) VALUES
  ('ch.protonmail.android', 'https://www.apkmirror.com/uploads/?appcategory=protonmail-encrypted-email', 'https://www.apkmirror.com/apk/proton-technologies-ag/protonmail-encrypted-email/proton-mail-encrypted-email-7-9-4-release/', NULL, NULL, 'manual'),
  ('ch.protonvpn.android', 'https://www.apkmirror.com/uploads/?appcategory=protonvpn-secure-and-free-vpn', 'https://www.apkmirror.com/apk/proton-technologies-ag/protonvpn-secure-and-free-vpn/proton-vpn-fast-secure-vpn-5-18-18-0-release/', NULL, NULL, 'manual'),
  ('cn.wps.moffice_eng', 'https://www.apkmirror.com/uploads/?appcategory=wps-office-pdf', 'https://www.apkmirror.com/apk/wps-software-pte-ltd/wps-office-pdf/wps-office-pdf-word-sheet-18-25-2-release/', NULL, NULL, 'manual'),
  ('com.adguard.android', 'https://www.apkmirror.com/uploads/?appcategory=adguard', 'https://www.apkmirror.com/apk/adguard-software-limited/adguard/adguard-for-android-4-12-81-release/', NULL, NULL, 'manual'),
  ('com.amazon.avod.thirdpartyclient', 'https://www.apkmirror.com/uploads/?appcategory=amazon-prime-video', 'https://www.apkmirror.com/apk/amazon-mobile-llc/amazon-prime-video/amazon-prime-video-3-0-447-release/', NULL, NULL, 'manual'),
  ('com.bilibili.app.in', 'https://www.apkmirror.com/uploads/?appcategory=bilibili', 'https://www.apkmirror.com/apk/bilibili/bilibili/bilibili-3-20-2-release/', NULL, NULL, 'manual'),
  ('com.brave.browser', 'https://www.apkmirror.com/uploads/?appcategory=brave-browser', 'https://www.apkmirror.com/apk/brave-software/brave-browser/brave-private-web-browser-vpn-1-92-140-release/', NULL, NULL, 'manual'),
  ('com.discord', 'https://www.apkmirror.com/uploads/?appcategory=discord-chat-for-gamers', 'https://www.apkmirror.com/apk/discord/discord-chat-for-gamers/discord-talk-play-hang-out-327-12-stable-release/', NULL, NULL, 'manual'),
  ('com.duolingo', 'https://www.apkmirror.com/uploads/?appcategory=duolingo-duolingo', 'https://www.apkmirror.com/apk/duolingo/duolingo-duolingo/duolingo-language-lessons-6-77-5-release/', NULL, NULL, 'manual'),
  ('com.facebook.katana', 'https://www.apkmirror.com/uploads/?appcategory=facebook', 'https://www.apkmirror.com/apk/facebook-2/facebook/facebook-490-0-0-63-82-release/', NULL, NULL, 'manual'),
  ('com.facebook.orca', 'https://www.apkmirror.com/uploads/?appcategory=messenger', 'https://www.apkmirror.com/apk/facebook-2/messenger/facebook-messenger-560-0-0-55-69-release/', NULL, NULL, 'manual'),
  ('com.google.android.apps.magazines', 'https://www.apkmirror.com/uploads/?appcategory=google-news', 'https://www.apkmirror.com/apk/google-inc/google-news/google-news-daily-headlines-5-158-0-908428942-release/', NULL, NULL, 'manual'),
  ('com.google.android.apps.photos', 'https://www.apkmirror.com/uploads/?appcategory=photos', 'https://www.apkmirror.com/apk/google-inc/photos/google-photos-7-74-0-910345644-release/', NULL, NULL, 'manual'),
  ('com.google.android.apps.recordero', 'https://www.apkmirror.com/uploads/?appcategory=google-recorder', 'https://www.apkmirror.com/apk/google-inc/google-recorder/google-recorder-4-2-20260409-906192856-release/', NULL, NULL, 'manual'),
  ('com.google.android.apps.youtube.music', 'https://www.apkmirror.com/uploads/?appcategory=youtube-music', 'https://www.apkmirror.com/apk/google-inc/youtube-music/youtube-music-9-17-51-release/', NULL, NULL, 'manual'),
  ('com.google.android.youtube', 'https://www.apkmirror.com/uploads/?appcategory=youtube', 'https://www.apkmirror.com/apk/google-inc/youtube/youtube-21-18-168-release/', NULL, NULL, 'manual'),
  ('com.instagram.android', 'https://www.apkmirror.com/uploads/?appcategory=instagram-instagram', 'https://www.apkmirror.com/apk/instagram/instagram-instagram/instagram-428-0-0-47-67-release/', NULL, NULL, 'manual'),
  ('com.instagram.barcelona', 'https://www.apkmirror.com/uploads/?appcategory=threads-an-instagram-app', 'https://www.apkmirror.com/apk/instagram/threads-an-instagram-app/threads-428-0-0-46-66-release/', NULL, NULL, 'manual'),
  ('com.intsig.camscanner', 'https://www.apkmirror.com/uploads/?appcategory=camscanner-phone-pdf-creator', 'https://www.apkmirror.com/apk/intsig-pte/camscanner-phone-pdf-creator/camscanner-scanner-pdf-maker-7-20-0-2606230000-release/', NULL, NULL, 'manual'),
  ('com.microblink.photomath', 'https://www.apkmirror.com/uploads/?appcategory=photomath', 'https://www.apkmirror.com/apk/google-inc/photomath/photomath-8-47-1-release/', NULL, NULL, 'manual'),
  ('com.paget96.batteryguru', 'https://www.apkmirror.com/uploads/?appcategory=battery-guru-health-saver', 'https://www.apkmirror.com/apk/paget96/battery-guru-health-saver/battery-guru-battery-health-2-4-8-1-release/', NULL, NULL, 'manual'),
  ('com.psiphon3.subscription', 'https://www.apkmirror.com/uploads/?appcategory=psiphon-pro-the-internet-freedom-vpn', 'https://www.apkmirror.com/apk/psiphon-inc/psiphon-pro-the-internet-freedom-vpn/psiphon-vpn-freedom-online-479-release/', NULL, NULL, 'manual'),
  ('com.rarlab.rar', 'https://www.apkmirror.com/uploads/?appcategory=rar', 'https://www.apkmirror.com/apk/rarlab-published-by-win-rar-gmbh/rar/rar-7-20-build131-release/', NULL, NULL, 'manual'),
  ('com.reddit.frontpage', 'https://www.apkmirror.com/uploads/?appcategory=reddit', 'https://www.apkmirror.com/apk/redditinc/reddit/reddit-2026-18-0-release/', NULL, NULL, 'manual'),
  ('com.soundcloud.android', 'https://www.apkmirror.com/uploads/?appcategory=soundcloud-soundcloud', 'https://www.apkmirror.com/apk/soundcloud/soundcloud-soundcloud/soundcloud-play-music-songs-2026-04-30-release-release/', NULL, NULL, 'manual'),
  ('com.ss.android.ugc.trill', 'https://www.apkmirror.com/uploads/?appcategory=tik-tok', 'https://www.apkmirror.com/apk/tiktok-pte-ltd/tik-tok/tiktok-43-8-3-2-release/', NULL, NULL, 'manual'),
  ('com.teslacoilsw.launcher', 'https://www.apkmirror.com/uploads/?appcategory=nova-launcher', 'https://www.apkmirror.com/apk/instabridge-sweden-ab/nova-launcher/nova-launcher-87300-8-7-3-release/', NULL, NULL, 'manual'),
  ('com.truecaller', 'https://www.apkmirror.com/uploads/?appcategory=truecaller-caller-id-block', 'https://www.apkmirror.com/apk/true-software-scandinavia-ab/truecaller-caller-id-block/truecaller-trusted-caller-id-26-17-6-release/', NULL, NULL, 'manual'),
  ('com.tumblr', 'https://www.apkmirror.com/uploads/?appcategory=tumblr', 'https://www.apkmirror.com/apk/tumblr-inc/tumblr/tumblr-social-media-art-blog-44-4-0-108-release/', NULL, NULL, 'manual'),
  ('com.twitter.android', 'https://www.apkmirror.com/uploads/?appcategory=twitter', 'https://www.apkmirror.com/apk/x-corp/twitter/x-12-19-1-release-0-release/', 'https://www.apkmirror.com/apk/x-corp/twitter/x-12-24-0-prod-02-release/', NULL, 'manual'),
  ('com.viber.voip', 'https://www.apkmirror.com/uploads/?appcategory=viber', 'https://www.apkmirror.com/apk/viber-media-s-a-r-l/viber/rakuten-viber-messenger-27-8-1-0-release/', NULL, NULL, 'manual'),
  ('com.waze', 'https://www.apkmirror.com/uploads/?appcategory=waze-gps-maps-traffic-alerts-live-navigation', 'https://www.apkmirror.com/apk/waze/waze-gps-maps-traffic-alerts-live-navigation/waze-navigation-live-traffic-5-21-0-0-release/', NULL, NULL, 'manual'),
  ('com.windscribe.vpn', 'https://www.apkmirror.com/uploads/?appcategory=windscribe-vpn', 'https://www.apkmirror.com/apk/windscribe/windscribe-vpn/windscribe-fast-secure-vpn-4-1-2274-release/', NULL, NULL, 'manual'),
  ('com.zhiliaoapp.musically', 'https://www.apkmirror.com/uploads/?appcategory=tik-tok-including-musical-ly', 'https://www.apkmirror.com/apk/tiktok-pte-ltd/tik-tok-including-musical-ly/tiktok-46-9-1-release/', NULL, NULL, 'manual'),
  ('com.zing.zalo', 'https://www.apkmirror.com/uploads/?appcategory=zalo', 'https://www.apkmirror.com/apk/zalo-group/zalo/zalo-26-07-01-release/', NULL, 'https://apkpure.com/zalo/com.zing.zalo/downloading/', 'manual'),
  ('ginlemon.flowerfree', 'https://www.apkmirror.com/uploads/?appcategory=smart-launcher', 'https://www.apkmirror.com/apk/smart-launcher-team/smart-launcher/smart-launcher-6-%e2%80%a7-home-screen-6-6-build-010-release/', NULL, NULL, 'manual'),
  ('jp.pxv.android', 'https://www.apkmirror.com/uploads/?appcategory=pixiv', 'https://www.apkmirror.com/apk/pixiv-inc/pixiv/pixiv-6-180-1-release/', NULL, NULL, 'manual'),
  ('net.dinglisch.android.taskerm', NULL, NULL, NULL, NULL, 'pending'),
  ('nextapp.fx', 'https://www.apkmirror.com/uploads/?appcategory=fx-file-explorer', 'https://www.apkmirror.com/apk/nextapp-inc/fx-file-explorer/fx-file-explorer-9-1-0-0-release/', NULL, NULL, 'manual'),
  ('pl.solidexplorer2', 'https://www.apkmirror.com/uploads/?appcategory=solid-explorer-file-manager', 'https://www.apkmirror.com/apk/neatbytes/solid-explorer-file-manager/solid-explorer-file-manager-3-4-10-release/', NULL, NULL, 'manual'),
  ('tv.twitch.android.app', 'https://www.apkmirror.com/uploads/?appcategory=twitch', 'https://www.apkmirror.com/apk/twitch-interactive-inc/twitch/twitch-live-streaming-29-3-1-release/', NULL, NULL, 'manual');

COMMIT;
PRAGMA foreign_keys=ON;
