package io.github.jhangyu.relume.ui

import io.github.jhangyu.relume.api.AppBundle
import io.github.jhangyu.relume.api.AppObject
import io.github.jhangyu.relume.api.AppStatusDto
import io.github.jhangyu.relume.api.Candidate
import io.github.jhangyu.relume.api.DownloadState
import io.github.jhangyu.relume.api.InstalledPackage
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.StepKind
import io.github.jhangyu.relume.install.step
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The Apps card's phone row (not installed / update / installed / blocked) and its download-URL fix. */
class InstallStateTest {
    private fun app(state: String = "up_to_date", version: String? = "1.0", lookup: String = "manual", candidates: Int = 0) = AppObject(
        id = 7, packageName = "p.item.7", name = "App", iconUrl = null,
        bundle = AppBundle(1, "Morphe Patches", "builtin", "morphe", "v1"), appVersion = version,
        status = AppStatusDto(state, null, null, null, null),
        download = DownloadState(
            lookup, null, null, null, null,
            List(candidates) { Candidate("App $it", "https://www.apkmirror.com/apk/o/a$it/", "https://www.apkmirror.com/uploads/?appcategory=a$it") },
            null, null,
        ),
    )

    private fun installed(version: String) = InstalledPackage("p.item.7", 1, version, "cd".repeat(32))

    @Test
    fun planStepDecidesWhenAssigned() {
        val install = step(StepKind.ITEM, 7)
        assertEquals(PhoneState.NotInstalled, AppFacts(app(), null, install).phone)
        assertEquals(PhoneState.Update, AppFacts(app(), installed("0.9"), install).phone)
        assertEquals(PhoneState.Installed, AppFacts(app(), installed("1.0"), step(StepKind.ITEM, 7, action = PlanAction.UP_TO_DATE)).phone)
        assertEquals(PhoneState.Blocked, AppFacts(app(), null, step(StepKind.ITEM, 7, action = PlanAction.ERROR)).phone)
        assertTrue(AppFacts(app(), installed("0.9"), install).installable)
    }

    @Test
    fun unassignedAppComparesTheBuiltVersion() {
        val fresh = AppFacts(app(), null, null)
        assertEquals(PhoneState.NotInstalled, fresh.phone)
        assertTrue(fresh.installable)   // Install assigns the app to this phone first
        assertEquals(PhoneState.Update, AppFacts(app(), installed("0.9"), null).phone)
        assertEquals(PhoneState.Installed, AppFacts(app(), installed("1.0"), null).phone)
        assertFalse(AppFacts(app(version = null, state = "not_built"), null, null).installable)
        assertFalse(AppFacts(app(state = "building"), null, null).installable)
    }

    @Test
    fun lookupOffersPickOrPaste() {
        assertEquals(Lookup.PickOne, AppFacts(app("needs_download_url", lookup = "ambiguous", candidates = 2), null, null).lookup)
        assertEquals(Lookup.NeedsUrl, AppFacts(app("needs_download_url", lookup = "not_found"), null, null).lookup)
        assertNull(AppFacts(app(), null, null).lookup)
        assertEquals("2 APKMirror pages match", AppFacts(app("needs_download_url", lookup = "ambiguous", candidates = 2), null, null).statusLine)
    }
}
