package io.github.jhangyu.relume.inventory

import io.github.jhangyu.relume.api.InventoryApp
import io.github.jhangyu.relume.api.InventoryReport
import kotlinx.serialization.Serializable

/** The last inventory the server accepted: its [rev] and the apps it now holds for this phone. */
@Serializable
data class InventorySnapshot(val rev: Long, val apps: List<InventoryApp>)

/** Pure: what to send in the sync `inventory` (plan 1.4). */
object InventoryDiff {

    /** First report (no snapshot): the whole list. Else the entries that differ from [previous] and the packages gone since. */
    fun report(previous: InventorySnapshot?, current: List<InventoryApp>): InventoryReport {
        if (previous == null) return full(current)
        val before = previous.apps.associateBy { it.packageName }
        val now = current.map { it.packageName }.toSet()
        return InventoryReport(
            baseRev = previous.rev,
            full = false,
            apps = current.filter { before[it.packageName] != it },
            removed = before.keys.filter { it !in now }.sorted(),
        )
    }

    /** Whole list; also the resend after the server answered `accepted=false` (its rev moved past ours). */
    fun full(current: List<InventoryApp>) = InventoryReport(baseRev = null, full = true, apps = current, removed = emptyList())
}
