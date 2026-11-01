package io.github.jhangyu.relume.sync

import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.StepKind
import io.github.jhangyu.relume.install.plan
import io.github.jhangyu.relume.install.step
import org.junit.Assert.assertEquals
import org.junit.Test

class BackgroundStartTest {
    @Test
    fun startsOnlyOnNewRevision() {
        val withInstall = plan(step(StepKind.ITEM, 1, action = PlanAction.UP_TO_DATE), step(StepKind.ITEM, 2))
        val noInstall = plan(step(StepKind.ITEM, 1, action = PlanAction.UP_TO_DATE), step(StepKind.PROJECT, 3, action = PlanAction.ERROR))
        val cases = listOf(
            Pair(withInstall, null) to true,
            Pair(withInstall, "older") to true,
            Pair(withInstall, "rev") to false,
            Pair(noInstall, null) to false,
            Pair(noInstall, "older") to false,
        )
        for ((input, want) in cases) {
            val (p, last) = input
            assertEquals("last=$last installs=${p === withInstall}", want, BackgroundStart.shouldStart(p, last))
        }
    }
}
