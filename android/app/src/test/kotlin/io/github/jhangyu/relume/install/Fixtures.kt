package io.github.jhangyu.relume.install

import io.github.jhangyu.relume.api.Download
import io.github.jhangyu.relume.api.InstallPlan
import io.github.jhangyu.relume.api.PlanAction
import io.github.jhangyu.relume.api.PlanStep
import io.github.jhangyu.relume.api.StepKind

internal fun download(kind: StepKind, ref: Int) = Download(
    url = "/api/x/$ref/download", fileName = "$ref.apk", size = 10, sha256 = "ab".repeat(32),
    signerSha256 = "cd".repeat(32), versionCode = 5, versionName = "1.0",
    artifactId = if (kind == StepKind.ITEM) ref + 100 else null, tag = if (kind == StepKind.ITEM) null else "v$ref",
)

internal fun step(kind: StepKind, ref: Int, needs: List<Int> = emptyList(), action: PlanAction = PlanAction.INSTALL) = PlanStep(
    kind = kind, refId = ref, packageName = "p.$kind.$ref", displayName = "$kind $ref", action = action,
    reason = if (action == PlanAction.ERROR) "NOT_DOWNLOADED" else "", needs = needs, signerConflict = false,
    downgrade = false, pullJobId = null, signerChange = null,
    download = if (action == PlanAction.INSTALL) download(kind, ref) else null,
)

internal fun plan(vararg steps: PlanStep) = InstallPlan("rev", steps.toList())
