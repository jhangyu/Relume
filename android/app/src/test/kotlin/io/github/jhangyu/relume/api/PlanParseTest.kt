package io.github.jhangyu.relume.api

import kotlinx.serialization.SerializationException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class PlanParseTest {
    private fun step(action: String = "install", omit: String? = null, sha: String = "ab".repeat(32), signer: String = "cd".repeat(32)): String {
        val fields = linkedMapOf(
            "kind" to "\"item\"", "ref_id" to "7", "package" to "\"com.example\"", "display_name" to "\"Example\"",
            "action" to "\"$action\"", "reason" to "\"\"", "needs" to "[3]", "signer_conflict" to "false",
            "downgrade" to "false", "pull_job_id" to "null", "signer_change" to "null",
            "download" to if (action == "install") {
                """{"url":"/api/artifacts/9/download","file_name":"a.apk","size":10,"sha256":"$sha","signer_sha256":"$signer",
                   "version_code":42,"version_name":"1.0","artifact_id":9}"""
            } else {
                "null"
            },
        )
        omit?.let { fields.remove(it) }
        return fields.entries.joinToString(",", "{", "}") { (k, v) -> "\"$k\":$v" }
    }

    private fun plan(step: String) = """{"revision":"r1","steps":[$step]}"""

    @Test
    fun missingFieldNamesField() {
        val ok = ApiJson.decodeFromString(InstallPlan.serializer(), plan(step()))
        assertEquals(9, ok.steps.single().download?.artifactId)
        for (field in listOf("display_name", "pull_job_id", "download")) {
            val e = assertThrows(SerializationException::class.java) {
                ApiJson.decodeFromString(InstallPlan.serializer(), plan(step(omit = field)))
            }
            assertTrue("message names $field: ${e.message}", e.message.orEmpty().contains(field))
        }
    }

    @Test
    fun downloadHashesMustBeLowercaseHex() {
        for ((field, bad) in listOf("sha256" to "../../shared_prefs/x", "sha256" to "AB".repeat(32), "signer_sha256" to "cd")) {
            val json = if (field == "sha256") step(sha = bad) else step(signer = bad)
            val e = assertThrows(IllegalArgumentException::class.java) { ApiJson.decodeFromString(InstallPlan.serializer(), plan(json)) }
            assertTrue("message names $field and '$bad': ${e.message}", e.message.orEmpty().contains("download.$field") && e.message.orEmpty().contains(bad))
        }
    }

    @Test
    fun unknownActionFails() {
        val e = assertThrows(SerializationException::class.java) {
            ApiJson.decodeFromString(InstallPlan.serializer(), plan(step(action = "reinstall")))
        }
        assertTrue("message names the value: ${e.message}", e.message.orEmpty().contains("reinstall"))
    }
}
