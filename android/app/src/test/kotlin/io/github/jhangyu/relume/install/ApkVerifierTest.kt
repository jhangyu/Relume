package io.github.jhangyu.relume.install

import io.github.jhangyu.relume.api.InstalledPackage
import io.github.jhangyu.relume.api.StepKind
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ApkVerifierTest {
    private val s = step(StepKind.ITEM, 1)
    private val d = s.download!!
    private val sha = d.sha256
    private val signer = d.signerSha256

    private fun code(m: Mismatch?, want: String, part: String) {
        assertEquals("$m", want, m?.code)
        assertTrue("$m", m!!.message.contains(part))
    }

    @Test
    fun hexCompareAndMismatchCodes() {
        assertTrue(VerifyRules.hexEquals("AB:CD:EF", "abcdef"))
        assertTrue(VerifyRules.hexEquals(" ABCDEF\n", "abcdef"))
        assertEquals(false, VerifyRules.hexEquals("abcdef", "abcde0"))
        assertEquals("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", VerifyRules.sha256Hex(ByteArray(0)))

        // step 1: headers and file hash; a missing header names it
        assertNull(VerifyRules.checkHashes(d, VerifyHeaders(sha.uppercase(), signer), sha))
        code(VerifyRules.checkHashes(d, VerifyHeaders(null, signer), sha), ErrorCodes.HASH_MISMATCH, "X-Relume-Sha256 missing")
        code(VerifyRules.checkHashes(d, VerifyHeaders(sha, null), sha), ErrorCodes.HASH_MISMATCH, "X-Relume-Signer-Sha256 missing")
        code(VerifyRules.checkHashes(d, VerifyHeaders("00", signer), sha), ErrorCodes.HASH_MISMATCH, "stale plan")
        code(VerifyRules.checkHashes(d, VerifyHeaders(sha, signer), "00"), ErrorCodes.HASH_MISMATCH, "file sha256 is 00")

        // step 2: archive facts
        val good = InstalledPackage(s.packageName, d.versionCode, "1.0", signer.uppercase())
        assertNull(VerifyRules.checkArchive(s, d, good))
        code(VerifyRules.checkArchive(s, d, null), ErrorCodes.SIGNER_MISMATCH_FILE, "not a readable APK")
        code(VerifyRules.checkArchive(s, d, good.copy(packageName = "x")), ErrorCodes.SIGNER_MISMATCH_FILE, "package x")
        code(VerifyRules.checkArchive(s, d, good.copy(versionCode = 4)), ErrorCodes.SIGNER_MISMATCH_FILE, "version code 4")
        code(VerifyRules.checkArchive(s, d, good.copy(signerSha256 = "ee")), ErrorCodes.SIGNER_MISMATCH_FILE, "signer ee")

        // step 3: installed package
        assertNull(VerifyRules.checkInstalled(d, null))
        assertNull(VerifyRules.checkInstalled(d, good.copy(versionCode = 4)))
        assertNull(VerifyRules.checkInstalled(d, good))
        code(VerifyRules.checkInstalled(d, good.copy(signerSha256 = "ee")), ErrorCodes.SIGNATURE_MISMATCH, "installed signer ee")
        code(VerifyRules.checkInstalled(d, good.copy(versionCode = 6)), ErrorCodes.VERSION_DOWNGRADE, "installed version code 6")
    }
}
