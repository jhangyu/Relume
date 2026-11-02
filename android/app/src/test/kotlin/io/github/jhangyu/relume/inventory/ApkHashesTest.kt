package io.github.jhangyu.relume.inventory

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The APK hash cache (decision 15): reused only while the installed file path and update time are unchanged. */
class ApkHashesTest {
    private val dir = "/data/app/~~a1==/com.example-b2==/base.apk"
    private val sha = "cd".repeat(32)

    @Test
    fun cacheHitsOnlyOnSameDirAndUpdateTime() {
        val stored = ApkHashes.entry(dir, 42L, sha)
        assertEquals(sha, ApkHashes.cachedHash(stored, dir, 42L))
        assertNull("updated in place", ApkHashes.cachedHash(stored, dir, 43L))
        assertNull("reinstalled to a new dir", ApkHashes.cachedHash(stored, "/data/app/~~c3==/com.example-d4==/base.apk", 42L))
        assertNull("nothing cached", ApkHashes.cachedHash(null, dir, 42L))
    }
}
