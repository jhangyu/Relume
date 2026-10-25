package io.github.jhangyu.relume.install

import android.content.Context
import android.util.AtomicFile
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.Json
import java.io.File

/**
 * The one install run of this process, persisted as `filesDir/install-run.json` (spec 9.1). The receiver (main thread)
 * and the worker change it; [update] serialises every change under one lock, so this object owns the state.
 */
internal object InstallRunStore {
    private const val FILE_NAME = "install-run.json"
    private val json = Json
    private val lock = Any()
    private var file: AtomicFile? = null
    private val flow = MutableStateFlow<RunState?>(null)

    val state: StateFlow<RunState?> = flow.asStateFlow()

    /** Reads the run file once per process. A corrupt file throws (no silent reset of a run in progress). */
    fun load(context: Context) {
        synchronized(lock) {
            if (file != null) return
            val f = AtomicFile(File(context.applicationContext.filesDir, FILE_NAME))
            flow.value = if (f.baseFile.exists()) {
                json.decodeFromString(RunState.serializer(), f.readFully().toString(Charsets.UTF_8))
            } else {
                null
            }
            file = f
        }
    }

    /** Applies [change] to the current run, writes the file, publishes the result and returns it. */
    fun update(context: Context, change: (RunState?) -> RunState?): RunState? {
        synchronized(lock) {
            load(context)
            val next = change(flow.value)
            if (next == flow.value) return next
            checkNotNull(next) { "install run: a change may not remove the run (current revision ${flow.value?.revision})" }
            val f = checkNotNull(file)
            val out = f.startWrite()
            try {
                out.write(json.encodeToString(RunState.serializer(), next).toByteArray(Charsets.UTF_8))
                f.finishWrite(out)
            } catch (e: Exception) {
                f.failWrite(out)
                throw e
            }
            flow.value = next
            return next
        }
    }
}
