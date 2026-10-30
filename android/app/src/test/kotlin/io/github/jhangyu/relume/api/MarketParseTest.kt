package io.github.jhangyu.relume.api

import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** JSON samples copied from API part 2.1/2.2/2.3 with the spec 6 amendments (stars, updated_at). */
class MarketParseTest {
    @Test
    fun marketplaceListParses() {
        val text = """
            {"total": 1207, "page": 1, "per_page": 50,
             "categories": [{"name": "Social", "count": 88}],
             "apps": [{"package": "com.reddit.frontpage", "name": "Reddit", "category": "Social",
                       "icon_url": "/api/apps/com.reddit.frontpage/icon", "bundle_count": 4, "stars": 1200,
                       "origins": ["builtin", "official"], "updated_at": "2026-10-20T10:00:00Z", "app_id": 12, "enabled": true}]}
        """
        val list = ApiJson.decodeFromString(MarketList.serializer(), text)
        val app = list.apps.single()
        assertEquals(1207, list.total)
        assertEquals(50, list.perPage)
        assertEquals("Social", list.categories.single().name)
        assertEquals("com.reddit.frontpage", app.packageName)
        assertEquals(4, app.bundleCount)
        assertEquals(listOf("builtin", "official"), app.origins)
        assertEquals(12, app.appId)
        assertTrue(app.enabled)
    }

    @Test
    fun catalogStatusParses() {
        val text = """
            {"feeds": [{"feed": "bundles.json", "origin": "official", "last_ok_at": "2026-10-26T08:00:00Z",
                        "last_attempt_at": "2026-10-26T08:00:00Z", "last_error": null, "bundle_count": 297,
                        "app_count": 1207, "duplicates": [],
                        "skipped": [{"where": "bundles[12]", "repo": "o/r", "error": "bad"}]}],
             "sources": [{"bundle_id": 1, "origin": "builtin", "repo": "MorpheApp/morphe-patches",
                          "list_tag": "v1.48.0", "last_error": null}]}
        """
        val feed = ApiJson.decodeFromString(FeedStatus.serializer(), text)
        assertEquals(297, feed.feeds.single().bundleCount)
        assertEquals("bundles[12]", feed.feeds.single().skipped.single().where)
        assertNull(feed.sources.single().lastError)
    }

    @Test
    fun appObjectParses() {
        val text = """
            {"id": 12, "package": "com.reddit.frontpage", "name": "Reddit", "icon_url": "/api/apps/com.reddit.frontpage/icon",
             "category": "Social & Communication",
             "bundle": {"bundle_id": 1, "name": "Morphe Patches", "origin": "builtin", "engine": "morphe", "tag": "v1.48.0"},
             "app_version": "2026.40.0",
             "status": {"state": "building", "job_id": 345, "progress": {"step": "patch", "index": 4, "count": 6},
                        "fail_code": null, "fail_detail": null},
             "download": {"state": "ambiguous", "apkmirror_list_url": null, "apkmirror_example_url": null,
                          "apkmirror_example_url2": null, "apkpure_url": null,
                          "candidates": [{"title": "YouTube", "url": "https://www.apkmirror.com/apk/google-inc/youtube/",
                                          "list_url": "https://www.apkmirror.com/uploads/?appcategory=youtube"}],
                          "error": null, "retry_at": null}}
        """
        val app = ApiJson.decodeFromString(AppObject.serializer(), text)
        assertEquals(12, app.id)
        assertEquals("Social & Communication", app.category)
        assertEquals("builtin", app.bundle.origin)
        assertEquals("v1.48.0", app.bundle.tag)
        assertEquals("building", app.status.state)
        assertEquals(4, app.status.progress?.index)
        assertNull(app.status.failCode)
        val candidate = app.download.candidates.single()
        assertEquals("YouTube", candidate.title)
        assertEquals("https://www.apkmirror.com/uploads/?appcategory=youtube", candidate.listUrl)
    }

    @Test
    fun appObjectWithoutCategoryParses() {
        val text = """
            {"id": 3, "package": "a.b", "name": "B", "icon_url": null, "category": null,
             "bundle": {"bundle_id": 1, "name": "M", "origin": "official", "engine": "morphe", "tag": null},
             "app_version": null,
             "status": {"state": "not_built", "job_id": null, "progress": null, "fail_code": null, "fail_detail": null},
             "download": {"state": "pending", "apkmirror_list_url": null, "apkmirror_example_url": null,
                          "apkmirror_example_url2": null, "apkpure_url": null, "candidates": [], "error": null, "retry_at": null}}
        """
        assertNull(ApiJson.decodeFromString(AppObject.serializer(), text).category)
    }

    @Test
    fun marketDetailKeepsUnknownDefaultEnabled() {
        val text = """
            {"package": "com.x", "name": "X", "category": "Tools", "description": "d", "icon_url": "/api/apps/com.x/icon",
             "app_id": null, "enabled": false,
             "bundles": [{"bundle_id": 1, "name": "Morphe Patches", "author": "MorpheApp", "host": "github",
                          "repo": "MorpheApp/morphe-patches", "origin": "builtin", "engine": "morphe", "stars": 1200,
                          "listed": true, "selectable": false, "not_selectable_reason": "GitLab download is not supported",
                          "updated_at": "2026-10-20T10:00:00Z", "versions": ["20.40.45"],
                          "patches": [{"name": "Hide ads", "description": "d", "default_enabled": null},
                                      {"name": "Theme", "description": null, "default_enabled": true}]}]}
        """
        val detail = ApiJson.decodeFromString(MarketDetail.serializer(), text)
        val bundle = detail.bundles.single()
        assertNull(detail.download)
        assertFalse(bundle.selectable)
        assertNull(bundle.patches[0].defaultEnabled)
        assertEquals(true, bundle.patches[1].defaultEnabled)
    }

    @Test
    fun scheduleParses() {
        val schedule = ApiJson.decodeFromString(
            Schedule.serializer(),
            """{"id": 1, "spec": {"every": "hours", "hours": 6}, "enabled": true, "options": {"notify": true},
                "last_run_at": "2026-10-26T06:00:00Z", "next_run_at": "2026-10-26T12:00:00Z"}""",
        )
        assertEquals(1, schedule.id)
        assertEquals("hours", schedule.spec.every)
        assertEquals(6, schedule.spec.hours)
        assertNull(schedule.spec.at)
        assertTrue(schedule.options.notify)
        assertEquals("2026-10-26T12:00:00Z", schedule.nextRunAt)

        val day = ApiJson.decodeFromString(
            Schedule.serializer(),
            """{"id": 1, "spec": {"every": "day", "at": "09:00"}, "enabled": false, "options": {"notify": false},
                "last_run_at": null, "next_run_at": null}""",
        )
        assertEquals("09:00", day.spec.at)
        assertNull(day.nextRunAt)
    }

    @Test
    fun jobCarriesItsSchedule() {
        val row = """{"id": 9, "kind": "upstream", "status": "done", "trigger": "schedule", "schedule_id": %s, "target": {"all": true},
            "options": {}, "reason": null, "created_at": "2026-10-26T06:00:00Z", "started_at": null, "finished_at": null, "detail": null}"""
        assertEquals(1, ApiJson.decodeFromString(Job.serializer(), row.format("1")).scheduleId)
        assertNull(ApiJson.decodeFromString(Job.serializer(), row.format("null")).scheduleId)
    }

    @Test
    fun appSettingsAndPatchesParse() {
        val settings = ApiJson.decodeFromString(
            AppSettingsEnvelope.serializer(),
            """{"id": 12, "settings": {"version": {"mode": "pinned", "pin": "20.40.45", "allow_nearest": false,
                 "supported": ["20.40.45"], "apkmirror": ["20.41.0", "20.40.45"]},
               "project_ids": [3], "display_name": null, "engine_options": null, "stale_overrides": ["Old patch"],
               "variants": [{"name": "arm64-v8a", "abi": "arm64-v8a", "download": {"kind": "apkmirror", "type": "apk"},
                             "exclude_splits": null, "strip_libs": "arm64-v8a"}]}}""",
        ).settings
        assertEquals(listOf("20.41.0", "20.40.45"), settings.version.apkmirror)
        assertEquals("arm64-v8a", (settings.variants.single()["abi"] as JsonPrimitive).content)
        assertNull(settings.engineOptions)
        assertEquals(listOf("Old patch"), settings.staleOverrides)

        val view = ApiJson.decodeFromString(
            PatchesView.serializer(),
            """{"tag": "v1.48.0", "files": [], "stale_overrides": [], "compatible_versions": ["20.40.45"], "unavailable": false,
                "patches": [{"name": "Custom branding", "description": "d", "default_enabled": true, "enabled": true,
                  "compatible_versions": [],
                  "options": [{"key": "appName", "title": "App name", "type": "String", "default": "YouTube",
                               "values": [], "value": "YT"},
                              {"key": "icon", "title": null, "type": null, "default": null, "values": ["a", "b"], "value": null}]}]}""",
        )
        val opts = view.patches.single().options
        assertEquals(JsonPrimitive("YT"), opts[0].value)
        assertEquals(JsonPrimitive("YouTube"), opts[0].default)
        assertEquals(listOf("a", "b"), opts[1].values)
        assertNull(opts[1].default)   // JSON null decodes to Kotlin null, not JsonNull
    }

    /** Regression: a build step without an `i/n` counter (`::step::verify`) broke `GET /api/apps` decoding. */
    @Test
    fun buildProgressWithoutCounterParses() {
        val p = ApiJson.decodeFromString(BuildProgress.serializer(), """{"step": "verify", "index": null, "count": null}""")
        assertEquals("verify", p.step)
        assertNull(p.fraction)
        assertEquals(0.5f, ApiJson.decodeFromString(BuildProgress.serializer(), """{"step": "patch", "index": 1, "count": 2}""").fraction)
    }
}
