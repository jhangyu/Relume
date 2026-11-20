package io.github.jhangyu.relume.action

import android.content.Context
import io.github.jhangyu.relume.api.ActionResult
import io.github.jhangyu.relume.api.DeviceAction
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/** Where a delivered action is on the phone. */
@Serializable
enum class ActionPhase {
    /** Not run yet. */
    @SerialName("new") NEW,
    /** `play_direct`: APKs being written into [StoredAction.sessionId]; seen in a later run = interrupted. */
    @SerialName("writing") WRITING,
    /** `play_direct`: session committed, its status broadcast is due. */
    @SerialName("committed") COMMITTED,
    /** `remove`: system uninstall requested, its status broadcast is due. */
    @SerialName("uninstalling") UNINSTALLING,
}

/** A delivered action kept until its [result] is posted (plan §1.12: the server sends it once). */
@Serializable
data class StoredAction(
    val action: DeviceAction,
    val phase: ActionPhase = ActionPhase.NEW,
    @SerialName("session_id") val sessionId: Int? = null,
    /** `play_direct`: the version code the server resolved. */
    @SerialName("version_code") val versionCode: Long? = null,
    /** Set when the action ended; the entry is dropped once the server took it (or answered 404/409). */
    val result: ActionResult? = null,
)

/** `filesDir/app-actions.json`, atomic replace; every change under one lock (the executor, the result receiver). */
internal object ActionStore {
    private const val FILE_NAME = "app-actions.json"
    private val lock = Any()
    private val StoreJson = Json { ignoreUnknownKeys = true }
    private val serializer = ListSerializer(StoredAction.serializer())
    private var cache: List<StoredAction>? = null

    fun all(context: Context): List<StoredAction> = synchronized(lock) { load(context) }

    fun get(context: Context, id: Long): StoredAction? = all(context).firstOrNull { it.action.id == id }

    /** Session ids of `play_direct` actions: the install run's cleanup must not abandon them. */
    fun sessionIds(context: Context): Set<Int> = all(context).mapNotNull { it.sessionId }.toSet()

    /** Stores newly delivered actions; an id already stored is kept as it is. */
    fun add(context: Context, actions: List<DeviceAction>) = change(context) { cur ->
        val have = cur.map { it.action.id }.toSet()
        cur + actions.filter { it.id !in have }.map { StoredAction(it) }
    }

    fun update(context: Context, id: Long, f: (StoredAction) -> StoredAction) = change(context) { cur ->
        check(cur.any { it.action.id == id }) { "app action store: action $id not stored" }
        cur.map { if (it.action.id == id) f(it) else it }
    }

    fun finish(context: Context, id: Long, result: ActionResult) = update(context, id) { it.copy(result = result) }

    fun remove(context: Context, ids: Set<Long>) = change(context) { cur -> cur.filter { it.action.id !in ids } }

    private fun change(context: Context, f: (List<StoredAction>) -> List<StoredAction>) {
        synchronized(lock) {
            val next = f(load(context))
            write(file(context), next)
            cache = next
        }
    }

    private fun file(context: Context) = File(context.applicationContext.filesDir, FILE_NAME)

    private fun load(context: Context): List<StoredAction> {
        cache?.let { return it }
        val f = file(context)
        val read = if (!f.exists()) emptyList() else try {
            StoreJson.decodeFromString(serializer, f.readText(Charsets.UTF_8))
        } catch (e: IllegalArgumentException) {
            // Delivered actions exist only here: an unreadable file is never replaced silently.
            throw IllegalStateException("app action store ${f.path}: expected a list of delivered actions, got unreadable content ($e)", e)
        }
        cache = read
        return read
    }

    private fun write(file: File, list: List<StoredAction>) {
        val temp = File(file.path + ".new")
        FileOutputStream(temp).use { out ->
            out.write(StoreJson.encodeToString(serializer, list).toByteArray(Charsets.UTF_8))
            out.fd.sync()
        }
        Files.move(temp.toPath(), file.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
    }
}
