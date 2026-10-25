package io.github.jhangyu.relume.sync

import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.StepKind
import io.github.jhangyu.relume.install.plan
import io.github.jhangyu.relume.install.step
import org.junit.Assert.assertEquals
import org.junit.Test

class BackgroundStartTest {
    @Test
    fun startsOnlyOnNewRevisionWithoutActiveRun() {
        val withInstall = plan(step(StepKind.ITEM, 1, action = PlanAction.UP_TO_DATE), step(StepKind.ITEM, 2))
        val noInstall = plan(step(StepKind.ITEM, 1, action = PlanAction.UP_TO_DATE), step(StepKind.PROJECT, 3, action = PlanAction.ERROR))
        val cases = listOf(
            Triple(withInstall, false, null) to true,
            Triple(withInstall, false, "older") to true,
            Triple(withInstall, false, "rev") to false,
            Triple(withInstall, true, null) to false,
            Triple(withInstall, true, "older") to false,
            Triple(noInstall, false, null) to false,
            Triple(noInstall, false, "older") to false,
        )
        for ((input, want) in cases) {
            val (p, active, last) = input
            assertEquals("active=$active last=$last installs=${p === withInstall}", want, BackgroundStart.shouldStart(p, active, last))
        }
    }
}
