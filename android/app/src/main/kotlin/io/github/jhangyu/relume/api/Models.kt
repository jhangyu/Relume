package io.github.jhangyu.relume.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject

/**
 * Wire format of every server response and request body. A missing field fails with its name (no default values
 * on response fields, except where the server sends one of two alternative keys and `init` checks which one).
 * Unknown keys are ignored: server objects carry fields the app does not read.
 */
internal val ApiJson = Json { ignoreUnknownKeys = true }

// ---- install plan (spec 5.1) ----

@Serializable
enum class StepKind {
    @SerialName("item") ITEM,
    @SerialName("project") PROJECT,
}

@Serializable
enum class PlanAction {
    @SerialName("install") INSTALL,
    @SerialName("up_to_date") UP_TO_DATE,
    @SerialName("error") ERROR,
}

@Serializable
data class InstallPlan(val revision: String, val steps: List<PlanStep>)

@Serializable
data class PlanStep(
    val kind: StepKind,
    @SerialName("ref_id") val refId: Int,
    @SerialName("package") val packageName: String,
    @SerialName("display_name") val displayName: String,
    val action: PlanAction,
    val reason: String,
    val needs: List<Int>,
    @SerialName("signer_conflict") val signerConflict: Boolean,
    val downgrade: Boolean,
    @SerialName("pull_job_id") val pullJobId: Int?,
    @SerialName("signer_change") val signerChange: SignerChange?,
    val download: Download?,
) {
    init {
        require((download != null) == (action == PlanAction.INSTALL)) {
            "plan step $kind $refId: download must be present exactly when action is install " +
                "(action=$action, download=${if (download == null) "null" else "present"})"
        }
        if (download != null) {
            val key = if (kind == StepKind.ITEM) download.artifactId else download.tag
            require(key != null) {
                "plan step $kind $refId: download.${if (kind == StepKind.ITEM) "artifact_id" else "tag"} is missing"
            }
        }
    }
}

@Serializable
data class Download(
    val url: String,
    @SerialName("file_name") val fileName: String,
    val size: Long,
    val sha256: String,
    @SerialName("signer_sha256") val signerSha256: String,
    @SerialName("version_code") val versionCode: Long,
    @SerialName("version_name") val versionName: String?,
    /** Present for item steps; [PlanStep] init checks it. */
    @SerialName("artifact_id") val artifactId: Int? = null,
    /** Present for project steps; [PlanStep] init checks it. */
    val tag: String? = null,
) {
    init {
        // sha256 names the download file (`<sha256>.part`): only 64 lowercase hex may reach a path.
        require(SHA256_HEX.matches(sha256)) { "download.sha256 must be 64 lowercase hex, got '$sha256'" }
        require(SHA256_HEX.matches(signerSha256)) { "download.signer_sha256 must be 64 lowercase hex, got '$signerSha256'" }
    }

    private companion object {
        val SHA256_HEX = Regex("[0-9a-f]{64}")
    }
}

@Serializable
data class SignerChange(val pinned: String?, val offered: String)

// ---- sync, receipts ----

@Serializable
data class InstalledPackage(
    @SerialName("package") val packageName: String,
    @SerialName("version_code") val versionCode: Long,
    @SerialName("version_name") val versionName: String?,
    @SerialName("signer_sha256") val signerSha256: String,
)

@Serializable
data class SyncDevice(
    val model: String,
    val sdk: Int,
    @SerialName("android_release") val androidRelease: String,
    @SerialName("abi_list") val abiList: List<String>,
    @SerialName("app_version_code") val appVersionCode: Long,
)

@Serializable
data class SyncReport(val device: SyncDevice, val packages: List<InstalledPackage>)

@Serializable
enum class ReceiptKind {
    @SerialName("item") ITEM,
    @SerialName("project") PROJECT,
}

@Serializable
enum class ReceiptResult {
    @SerialName("installed") INSTALLED,
    @SerialName("failed") FAILED,
}

@Serializable
data class InstallReceipt(
    val kind: ReceiptKind,
    @SerialName("ref_id") val refId: Int,
    val result: ReceiptResult,
    @SerialName("artifact_id") val artifactId: Int? = null,
    val tag: String? = null,
    @SerialName("error_code") val errorCode: String? = null,
    @SerialName("error_text") val errorText: String? = null,
) {
    init {
        when (kind) {
            ReceiptKind.ITEM -> require(artifactId != null && tag == null) {
                "item receipt $refId needs artifact_id and no tag (artifact_id=$artifactId, tag=$tag)"
            }
            ReceiptKind.PROJECT -> require(tag != null && artifactId == null) {
                "project receipt $refId needs tag and no artifact_id (artifact_id=$artifactId, tag=$tag)"
            }
        }
        require((errorCode != null) == (result == ReceiptResult.FAILED)) {
            "receipt $kind $refId: error_code must be set exactly when result is failed (result=$result, error_code=$errorCode)"
        }
    }
}

// ---- devices ----

/** Device facts sent once at registration (`POST /api/auth/device`). */
@Serializable
data class DeviceInfo(
    val name: String,
    val model: String,
    val sdk: Int,
    @SerialName("android_release") val androidRelease: String,
    @SerialName("abi_list") val abiList: List<String>,
)

@Serializable
enum class DeviceKind {
    @SerialName("adb") ADB,
    @SerialName("app") APP,
}

/** Device row as returned by registration, adopt and assignment updates. */
@Serializable
data class Device(
    val id: Int,
    val name: String,
    val kind: DeviceKind,
    val model: String?,
    val sdk: Int?,
    @SerialName("android_release") val androidRelease: String?,
    @SerialName("app_version_code") val appVersionCode: Long?,
    @SerialName("created_at") val createdAt: String,
    @SerialName("last_seen_at") val lastSeenAt: String?,
)

/** The `GET /api/devices` device object, also the `device` of `/api/me` and `/api/me/sync`. */
@Serializable
data class DeviceView(
    val id: Int,
    val name: String,
    val kind: DeviceKind,
    val model: String?,
    val sdk: Int?,
    @SerialName("android_release") val androidRelease: String?,
    @SerialName("app_version_code") val appVersionCode: Long?,
    @SerialName("created_at") val createdAt: String,
    @SerialName("last_seen_at") val lastSeenAt: String?,
    val assignments: List<Assignment>,
    val projects: List<DeviceProject>,
)

@Serializable
data class Assignment(@SerialName("item_id") val itemId: Int, val pending: Boolean)

@Serializable
data class DeviceProject(
    @SerialName("project_id") val projectId: Int,
    val assigned: Boolean,
    val pending: Boolean,
)

@Serializable
data class AdbDevice(val id: Int, val name: String, val model: String?)

@Serializable
data class Registration(
    val token: String,
    val device: Device,
    @SerialName("adb_devices") val adbDevices: List<AdbDevice>,
)

@Serializable
data class Me(val device: DeviceView, @SerialName("tracked_packages") val trackedPackages: List<String>)

@Serializable
data class SyncResponse(
    val device: DeviceView,
    @SerialName("tracked_packages") val trackedPackages: List<String>,
    val plan: InstallPlan,
)

@Serializable
data class Health(
    val ok: Boolean,
    val version: String,
)

// ---- marketplace, enabled apps, schedules (spec 6) ----

@Serializable
data class MarketList(
    val total: Int,
    val page: Int,
    @SerialName("per_page") val perPage: Int,
    val categories: List<MarketCategory>,
    val apps: List<MarketApp>,
    val feed: FeedStatus,
)

@Serializable
data class MarketCategory(val name: String, val count: Int)

@Serializable
data class MarketApp(
    @SerialName("package") val packageName: String,
    val name: String,
    val category: String?,
    @SerialName("icon_url") val iconUrl: String?,
    @SerialName("bundle_count") val bundleCount: Int,
    val stars: Int?,
    val origins: List<String>,
    @SerialName("updated_at") val updatedAt: String?,
    @SerialName("app_id") val appId: Int?,
    val enabled: Boolean,
)

@Serializable
data class MarketDetail(
    @SerialName("package") val packageName: String,
    val name: String,
    val category: String?,
    val description: String?,
    @SerialName("icon_url") val iconUrl: String?,
    @SerialName("app_id") val appId: Int?,
    val enabled: Boolean,
    /** The server sends `download` only when an app row exists for the package. */
    val download: DownloadState? = null,
    val bundles: List<BundleOption>,
)

@Serializable
data class BundleOption(
    @SerialName("bundle_id") val bundleId: Int,
    val name: String,
    val author: String?,
    val host: String,
    val repo: String,
    val origin: String,
    val engine: String,
    val stars: Int?,
    val listed: Boolean,
    val selectable: Boolean,
    @SerialName("not_selectable_reason") val notSelectableReason: String?,
    @SerialName("updated_at") val updatedAt: String?,
    val versions: List<String>,
    val patches: List<CatalogPatch>,
)

/** [defaultEnabled] is null when the catalog does not say (the UI shows "default unknown"). */
@Serializable
data class CatalogPatch(
    val name: String,
    val description: String?,
    @SerialName("default_enabled") val defaultEnabled: Boolean?,
)

@Serializable
data class FeedStatus(val feeds: List<FeedEntry>, val sources: List<FeedSource>)

@Serializable
data class FeedEntry(
    val feed: String,
    val origin: String,
    @SerialName("last_ok_at") val lastOkAt: String?,
    @SerialName("last_attempt_at") val lastAttemptAt: String?,
    @SerialName("last_error") val lastError: String?,
    @SerialName("bundle_count") val bundleCount: Int,
    @SerialName("app_count") val appCount: Int,
    val skipped: List<FeedSkip>,
)

@Serializable
data class FeedSkip(val where: String, val repo: String?, val error: String)

@Serializable
data class FeedSource(
    @SerialName("bundle_id") val bundleId: Int,
    val origin: String,
    val repo: String,
    @SerialName("list_tag") val listTag: String?,
    @SerialName("last_error") val lastError: String?,
)

/** One enabled app (`item` row): list entry and detail head. */
@Serializable
data class AppObject(
    val id: Int,
    @SerialName("package") val packageName: String,
    val name: String,
    @SerialName("icon_url") val iconUrl: String?,
    val bundle: AppBundle,
    @SerialName("app_version") val appVersion: String?,
    val status: AppStatusDto,
    val download: DownloadState,
)

@Serializable
data class AppBundle(
    @SerialName("bundle_id") val bundleId: Int,
    val name: String,
    val origin: String,
    val engine: String,
    val tag: String?,
)

@Serializable
data class AppStatusDto(
    /** up_to_date, update_available, building, queued, failed, not_built, needs_download_url. */
    val state: String,
    @SerialName("job_id") val jobId: Int?,
    val progress: BuildProgress?,
    @SerialName("fail_code") val failCode: String?,
    @SerialName("fail_detail") val failDetail: String?,
)

@Serializable
data class BuildProgress(val step: String, val index: Int, val count: Int)

/** APKMirror / APKPure download source of an app (not the install-plan [Download]). */
@Serializable
data class DownloadState(
    val state: String,
    @SerialName("apkmirror_list_url") val apkmirrorListUrl: String?,
    @SerialName("apkmirror_example_url") val apkmirrorExampleUrl: String?,
    @SerialName("apkmirror_example_url2") val apkmirrorExampleUrl2: String?,
    @SerialName("apkpure_url") val apkpureUrl: String?,
    val candidates: List<Candidate>,
    val error: String?,
    @SerialName("retry_at") val retryAt: String?,
)

@Serializable
data class Candidate(val title: String, val url: String, @SerialName("list_url") val listUrl: String)

/** `GET /api/apps/{id}`: the [AppObject] fields plus `settings`; [RelumeApi.app] decodes both from one body. */
data class AppDetail(val app: AppObject, val settings: AppSettings)

@Serializable
data class AppSettings(
    val version: VersionSettings,
    @SerialName("project_ids") val projectIds: List<Int>,
)

@Serializable
data class VersionSettings(
    /** auto, pinned or latest. */
    val mode: String,
    val pin: String?,
    @SerialName("allow_nearest") val allowNearest: Boolean,
    val supported: List<String>,
)

@Serializable
internal data class AppSettingsEnvelope(val settings: AppSettings)

/** `GET /api/apps/{id}/patches` (today's shape). */
@Serializable
data class PatchesView(
    val tag: String?,
    val patches: List<PatchView>,
    @SerialName("stale_overrides") val staleOverrides: List<String>,
    @SerialName("compatible_versions") val compatibleVersions: List<String>,
    val unavailable: Boolean,
)

@Serializable
data class PatchView(
    val name: String,
    val description: String?,
    @SerialName("default_enabled") val defaultEnabled: Boolean?,
    val enabled: Boolean?,
    @SerialName("compatible_versions") val compatibleVersions: List<String>,
)

/** `spec` of a schedule: `hours` for every=hours, `at` for day and week, `days` for week. */
@Serializable
data class ScheduleSpec(
    val every: String,
    val hours: Int? = null,
    val at: String? = null,
    val days: List<Int>? = null,
)

@Serializable
data class ScheduleOptions(val notify: Boolean)

/** The single global update schedule (`GET /api/schedule`). */
@Serializable
data class Schedule(
    val spec: ScheduleSpec,
    val enabled: Boolean,
    val options: ScheduleOptions,
    @SerialName("last_run_at") val lastRunAt: String?,
    @SerialName("next_run_at") val nextRunAt: String?,
)

@Serializable
data class OtaOptions(val retries: Int? = null, val notify: Boolean? = null)

@Serializable
data class OtaSchedule(
    val id: Int,
    val name: String,
    val target: JsonObject,
    val spec: ScheduleSpec,
    val options: OtaOptions,
    val enabled: Boolean,
    @SerialName("last_run_at") val lastRunAt: String?,
    @SerialName("next_run_at") val nextRunAt: String?,
)

@Serializable
data class CustomSource(
    @SerialName("bundle_id") val bundleId: Int,
    val repo: String,
    val engine: String,
)

@Serializable
data class Artifact(
    val id: Int,
    @SerialName("job_id") val jobId: Int,
    @SerialName("item_id") val itemId: Int,
    val variant: String,
    val abi: String,
    @SerialName("package") val packageName: String?,
    @SerialName("version_name") val versionName: String?,
    @SerialName("version_code") val versionCode: Long?,
    @SerialName("signer_sha256") val signerSha256: String?,
    val size: Long?,
    val sha256: String?,
    @SerialName("created_at") val createdAt: String,
    @SerialName("file_name") val fileName: String,
    @SerialName("download_url") val downloadUrl: String,
)

// ---- jobs, projects ----

@Serializable
data class Job(
    val id: Int,
    val kind: String,
    val status: String,
    val trigger: String,
    val target: JsonObject,
    val reason: String?,
    @SerialName("created_at") val createdAt: String,
    @SerialName("started_at") val startedAt: String?,
    @SerialName("finished_at") val finishedAt: String?,
)

@Serializable
data class Project(
    val id: Int,
    val label: String,
    val repo: String,
    @SerialName("package") val packageName: String?,
    @SerialName("downloaded_tag") val downloadedTag: String?,
    @SerialName("latest_tag") val latestTag: String?,
    @SerialName("check_error") val checkError: String?,
    @SerialName("signer_sha256") val signerSha256: String?,
    @SerialName("signer_alert") val signerAlert: String?,
)

// ---- response wrappers ----

@Serializable
internal data class DeviceEnvelope(val device: Device)

@Serializable
internal data class PlanEnvelope(val plan: InstallPlan)

@Serializable
internal data class ArtifactList(val artifacts: List<Artifact>)

@Serializable
internal data class AppList(val apps: List<AppObject>)

@Serializable
internal data class OtaScheduleList(val schedules: List<OtaSchedule>)

@Serializable
internal data class CreatedId(val id: Int)

@Serializable
internal data class BundleCreated(@SerialName("bundle_id") val bundleId: Int)

@Serializable
internal data class DeviceList(val devices: List<DeviceView>)

@Serializable
internal data class SourceList(val sources: List<CustomSource>)

@Serializable
internal data class JobList(val jobs: List<Job>)

@Serializable
internal data class ProjectList(val projects: List<Project>)

@Serializable
data class JobId(@SerialName("job_id") val jobId: Int)

/** `/api/cleanup` body: a preview (GET) or what was deleted (POST). */
@Serializable
data class CleanupReport(
    val bytes: Long,
    @SerialName("artifact_items") val artifactItems: Int,
    @SerialName("cache_files") val cacheFiles: Int,
)
