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
    @SerialName("companion") COMPANION,
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
    /** Present for project and companion steps; [PlanStep] init checks it. */
    val tag: String? = null,
)

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
    @SerialName("companion_version_code") val companionVersionCode: Long?,
)

// ---- catalog, items, artifacts ----

@Serializable
data class Catalog(val groups: List<Group>, val ungrouped: List<Item>)

@Serializable
data class Group(val id: Int, val name: String, val color: String?, val items: List<Item>)

@Serializable
data class Item(
    val id: Int,
    val label: String,
    @SerialName("package") val packageName: String,
    val enabled: Boolean,
    @SerialName("auto_update") val autoUpdate: Boolean,
    @SerialName("icon_url") val iconUrl: String?,
    val abis: List<String>,
    val upstream: ItemUpstream,
    @SerialName("last_build") val lastBuild: LastBuild?,
    @SerialName("last_success") val lastSuccess: LastSuccess?,
    @SerialName("active_job") val activeJob: ActiveJob?,
)

@Serializable
data class ItemUpstream(
    @SerialName("downloaded_tag") val downloadedTag: String?,
    @SerialName("latest_tag") val latestTag: String?,
    @SerialName("checked_at") val checkedAt: String?,
    @SerialName("check_error") val checkError: String?,
)

@Serializable
data class LastBuild(@SerialName("job_id") val jobId: Int, val status: String, val reason: String?)

@Serializable
data class LastSuccess(
    @SerialName("job_id") val jobId: Int,
    @SerialName("app_version") val appVersion: String?,
    @SerialName("finished_at") val finishedAt: String?,
)

@Serializable
data class ActiveJob(
    @SerialName("job_id") val jobId: Int,
    val status: String,
    val step: String?,
    @SerialName("step_index") val stepIndex: Int?,
    @SerialName("step_count") val stepCount: Int?,
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
internal data class JobList(val jobs: List<Job>)

@Serializable
internal data class ProjectList(val projects: List<Project>)

@Serializable
internal data class JobId(@SerialName("job_id") val jobId: Int)

@Serializable
internal data class JobIds(@SerialName("job_ids") val jobIds: List<Int>)
