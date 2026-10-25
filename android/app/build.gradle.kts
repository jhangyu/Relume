// AGP 9 built-in Kotlin brings kotlin-gradle-plugin 2.2.10; this pins it to the Compose and serialization plugin version.
buildscript {
    repositories {
        mavenCentral()
    }
    dependencies {
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:2.4.21")
    }
}

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
}

val releaseEnvNames = listOf(
    "RELUME_COMPANION_VERSION_CODE",
    "RELUME_COMPANION_VERSION_NAME",
    "RELUME_COMPANION_KEYSTORE",
    "RELUME_COMPANION_KEYSTORE_PASSWORD",
    "RELUME_COMPANION_KEY_ALIAS",
    "RELUME_COMPANION_KEY_PASSWORD",
)
val releaseEnv = releaseEnvNames.associateWith { providers.environmentVariable(it).orNull }

val checkReleaseEnv = tasks.register("checkReleaseEnv") {
    val values = releaseEnv
    doLast {
        val missing = values.filterValues { it.isNullOrEmpty() }.keys
        if (missing.isNotEmpty()) {
            throw GradleException("release build needs environment variables ${missing.joinToString()}: missing or empty")
        }
    }
}

android {
    namespace = "io.github.jhangyu.relume"
    compileSdk = 37

    defaultConfig {
        applicationId = "io.github.jhangyu.relume"
        minSdk = 29
        targetSdk = 37
        versionCode = 1
        versionName = "dev"
    }

    signingConfigs {
        create("release") {
            // Unset values are reported by checkReleaseEnv, which runs before every release task.
            releaseEnv["RELUME_COMPANION_KEYSTORE"]?.let { storeFile = file(it) }
            storePassword = releaseEnv["RELUME_COMPANION_KEYSTORE_PASSWORD"]
            keyAlias = releaseEnv["RELUME_COMPANION_KEY_ALIAS"]
            keyPassword = releaseEnv["RELUME_COMPANION_KEY_PASSWORD"]
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("release")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

androidComponents {
    onVariants(selector().withBuildType("release")) { variant ->
        variant.outputs.forEach { output ->
            output.versionCode.set(providers.environmentVariable("RELUME_COMPANION_VERSION_CODE").map { it.toInt() })
            output.versionName.set(providers.environmentVariable("RELUME_COMPANION_VERSION_NAME"))
        }
    }
}

tasks.configureEach {
    if (name == "preReleaseBuild" || name == "validateSigningRelease") {
        dependsOn(checkReleaseEnv)
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2026.09.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.core:core-ktx:1.19.1")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.11.0")
    implementation("androidx.work:work-runtime-ktx:2.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.12.0")
    testImplementation("junit:junit:4.13.2")
}
