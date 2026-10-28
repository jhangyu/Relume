package io.github.jhangyu.relume.ui

import io.github.jhangyu.relume.api.AppBundle
import io.github.jhangyu.relume.api.AppObject
import io.github.jhangyu.relume.api.AppStatusDto
import io.github.jhangyu.relume.api.Candidate
import io.github.jhangyu.relume.api.DownloadState
import io.github.jhangyu.relume.api.InstalledPackage
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The Apps card's one action (spec install-states 1) and its download-URL fix. */
class InstallStateTest {
    private val signer = "cd".repeat(32)

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

    // The built APK is a renamed package: the comparisons must use it, not the source app's p.item.7.
    private val target = InstallTarget("p.renamed.7", versionCode = 5, versionName = "1.0", patchVersion = "v1.25.2", signerSha256 = signer)

    private fun installed(code: Long, signerSha256: String = signer) = InstalledPackage("p.renamed.7", code, "x", signerSha256)

    @Test
    fun cardActionFollowsTheFourConditionsInOrder() {
        assertEquals(CardAction.Install, cardAction(target, null, null))
        // A different signer wins over an older version: Android cannot update in place.
        assertEquals(CardAction.Reinstall, cardAction(target, installed(4, "ef".repeat(32)), "v1.0.0"))
        assertEquals(CardAction.Installed, cardAction(target, installed(5, signer.uppercase()), "v1.25.2"))   // hex case does not matter
        assertEquals(CardAction.Upgrade, cardAction(target, installed(4), "v1.25.2"))                       // app version older
        assertEquals(CardAction.Upgrade, cardAction(target, installed(5), "v1.25.1"))                       // patch version older
        assertEquals(CardAction.Installed, cardAction(target, installed(6), "v1.26.0"))                     // phone is newer: no downgrade offer
        assertEquals(CardAction.Installed, cardAction(target, installed(5), null))                          // no record: patch not compared
        assertEquals(CardAction.Installed, cardAction(target.copy(signerSha256 = null, versionCode = null, patchVersion = null), installed(1), "v0"))
    }

    @Test
    fun patchVersionComparesLikeTheServer() {
        // Same cases as catalog.version_key (local/webui/catalog.py).
        assertTrue(versionOlder("v1.9.2", "v1.10.0"))       // numeric, not text order
        assertTrue(versionOlder("1.48.0-dev.3", "1.48.0"))  // prerelease before release
        assertTrue(versionOlder("v1.0-rc1", "v1.0"))
        assertFalse(versionOlder("v2.0", "2.0"))            // one leading v is dropped
        assertFalse(versionOlder("1.48.0", "1.48.0-dev.3"))
        assertTrue(versionOlder("1.2", "1.2.1"))            // shorter prefix is older
        assertFalse(versionOlder("20251231000000", "1"))    // date-style tags compare as numbers
    }

    @Test
    fun lookupOffersPickOrPaste() {
        assertEquals(Lookup.PickOne, AppFacts(app("needs_download_url", lookup = "ambiguous", candidates = 2), null, null).lookup)
        assertEquals(Lookup.NeedsUrl, AppFacts(app("needs_download_url", lookup = "not_found"), null, null).lookup)
        assertNull(AppFacts(app(), null, null).lookup)
        assertEquals("2 APKMirror pages match", AppFacts(app("needs_download_url", lookup = "ambiguous", candidates = 2), null, null).statusLine)
    }
}
