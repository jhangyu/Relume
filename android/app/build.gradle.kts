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

// APKs are named relume-<buildType>.apk.
base.archivesName = "relume"

val SIGNING_STORE_PASSWORD = "morphe"
val SIGNING_KEY_ALIAS = "fiorenmas"
val SIGNING_KEY_PASSWORD = "morphe"

val releaseEnvNames = listOf(
    "RELUME_COMPANION_VERSION_CODE",
    "RELUME_COMPANION_VERSION_NAME",
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
            // The committed patch keystore with its public credentials (src/build/build-item.sh): personal use, no secret key.
            storeFile = rootProject.file("../src/keystore/lspatch-signing.p12")
            storeType = "PKCS12"
            storePassword = SIGNING_STORE_PASSWORD
            keyAlias = SIGNING_KEY_ALIAS
            keyPassword = SIGNING_KEY_PASSWORD
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
            output.versionCode.set(providers.environmentVariable("RELUME_COMPANION_VERSION_CODE").map { v -> v.toIntOrNull() ?: error("RELUME_COMPANION_VERSION_CODE: expected an integer, got '$v'") })
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
