package io.github.jhangyu.relume.install

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.Json
import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/** The run file exists but does not hold a run; the UI shows [message] and offers a reset (spec 9.1). */
class CorruptRunFile(val path: String, cause: Exception) :
    Exception("install run file $path: expected a saved install run, got unreadable content ($cause)", cause)

/** One run file on disk: atomic replace via a temp file and rename. */
internal class RunFile(private val file: File) {
    private val temp = File(file.path + ".new")

    /** null = no file. Throws [CorruptRunFile] when the file is not a valid run. */
    fun read(): RunState? {
        if (!file.exists()) return null
        return try {
            Json.decodeFromString(RunState.serializer(), file.readText(Charsets.UTF_8))
        } catch (e: IllegalArgumentException) {
            // SerializationException (bad JSON or fields) and model `init` checks (e.g. a receipt without its key).
            throw CorruptRunFile(file.path, e)
        }
    }

    fun write(run: RunState) {
        FileOutputStream(temp).use { out ->
            out.write(Json.encodeToString(RunState.serializer(), run).toByteArray(Charsets.UTF_8))
            out.fd.sync()
        }
        Files.move(temp.toPath(), file.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
    }

    fun delete() {
        check(!file.exists() || file.delete()) { "install run file ${file.path}: cannot delete" }
    }
}

/**
 * The one install run of this process, persisted as `filesDir/install-run.json` (spec 9.1). The receiver (main thread)
 * and the worker change it; [update] serialises every change under one lock, so this object owns the state.
 */
internal object InstallRunStore {
    private const val FILE_NAME = "install-run.json"
    private val lock = Any()
    private var file: RunFile? = null
    private val flow = MutableStateFlow<RunState?>(null)
    private val corruptFlow = MutableStateFlow<CorruptRunFile?>(null)

    val state: StateFlow<RunState?> = flow.asStateFlow()

    /** Set while the run file is corrupt; [update] refuses to overwrite it until [reset]. */
    val corrupt: StateFlow<CorruptRunFile?> = corruptFlow.asStateFlow()

    /** Reads the run file once per process. A corrupt file is kept and reported via [corrupt], never silently replaced. */
    fun load(context: Context) {
        synchronized(lock) {
            if (file != null) return
            val f = RunFile(File(context.applicationContext.filesDir, FILE_NAME))
            try {
                flow.value = f.read()
            } catch (e: CorruptRunFile) {
                corruptFlow.value = e
            }
            file = f
        }
    }

    /** Applies [change] to the current run, writes the file, publishes the result and returns it. Throws [CorruptRunFile]. */
    fun update(context: Context, change: (RunState?) -> RunState?): RunState? {
        synchronized(lock) {
            load(context)
            corruptFlow.value?.let { throw it }
            val next = change(flow.value)
            if (next == flow.value) return next
            checkNotNull(next) { "install run: a change may not remove the run (current revision ${flow.value?.revision})" }
            checkNotNull(file).write(next)
            flow.value = next
            return next
        }
    }

    /** User reset after [corrupt]: deletes the run file; the next run starts empty. */
    fun reset(context: Context) {
        synchronized(lock) {
            load(context)
            checkNotNull(file).delete()
            flow.value = null
            corruptFlow.value = null
        }
    }
}
