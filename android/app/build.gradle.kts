import groovy.json.JsonSlurper
import java.text.SimpleDateFormat
import java.util.Date

// The debug channel's webhook comes from secrets.json (next to pokehunt.py), which isn't in the
// repository; without it the app reports nowhere until its config says "debug_webhook".
val secrets = rootProject.file("../secrets.json").takeIf { it.exists() }
    ?.let { JsonSlurper().parse(it) as Map<*, *> } ?: emptyMap<String, Any>()
val debugWebhook = secrets["android_debug_webhook"] as String? ?: ""

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.pokehunt"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.pokehunt"
        minSdk = 30  // screenshots from an accessibility service
        targetSdk = 35
        // every build is newer than the last, and says when it was built
        // (shown at the bottom of the app), so it's clear which one a phone has
        versionCode = (System.currentTimeMillis() / 60000 - 29_000_000).toInt()
        versionName = SimpleDateFormat("d MMM HH:mm").format(Date())
        // every phone of the last years: half the download of all four kinds
        ndk { abiFilters += "arm64-v8a" }
        buildConfigField("String", "DEBUG_WEBHOOK", "\"$debugWebhook\"")
    }
    buildFeatures { buildConfig = true }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("debug")  // installable as is
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    testOptions {
        unitTests.isReturnDefaultValues = true  // android.util.Log in tests
    }
}

dependencies {
    implementation("androidx.core:core:1.13.1")
    implementation("com.google.mlkit:text-recognition:16.0.1")  // bundled: works offline, no Play Services needed
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
