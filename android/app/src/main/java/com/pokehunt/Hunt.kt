package com.pokehunt

import android.content.ComponentName
import android.content.Context
import android.service.quicksettings.TileService
import java.io.File

/** Starting and stopping, for the app's button and the Quick Settings tile. */
object Hunt {
    fun configFile(ctx: Context): File {
        val f = File(ctx.filesDir, "config.json")
        if (!f.exists()) f.writeText(ctx.assets.open("config.json").bufferedReader().readText())
        return f
    }

    fun prefs(ctx: Context) = ctx.getSharedPreferences("ui", Context.MODE_PRIVATE)

    /** This phone's secret for the website, made the first time. */
    fun deviceKey(ctx: Context): String = prefs(ctx).getString("device_key", null) ?: run {
        val bytes = ByteArray(32).also { java.security.SecureRandom().nextBytes(it) }
        val key = android.util.Base64.encodeToString(bytes, android.util.Base64.URL_SAFE or android.util.Base64.NO_WRAP or
                                                     android.util.Base64.NO_PADDING)
        prefs(ctx).edit().putString("device_key", key).apply()
        key
    }

    fun watchOnly(ctx: Context) = prefs(ctx).getBoolean("watch", false)

    /** Catches every encounter, then saves; screenshots each catch turn. */
    fun debugMode(ctx: Context) = prefs(ctx).getBoolean("debug", false)

    val running get() = HuntService.instance?.hunter != null

    /** Starts a hunt. Blocks for a moment (it checks that screenshots work),
     *  so not on the main thread. Null once started, else what's missing:
     *  "service" (off in Accessibility), "capture" (needs screen capture
     *  allowed first) or the config's problem. */
    fun start(ctx: Context): String? {
        val service = HuntService.instance ?: return "service"
        val cfg = try {
            Config(configFile(ctx).readText())
        } catch (e: Exception) {
            return "What to hunt has a mistake: ${e.message}"
        }
        if (CaptureService.instance == null && !service.screenshotsWork()) return "capture"
        service.startHunt(cfg, watchOnly(ctx), debugMode(ctx))
        return null
    }

    fun stop() {
        HuntService.instance?.stopHunt()
    }

    /** The tile shows whether a hunt is on. */
    fun refreshTile(ctx: Context) =
        TileService.requestListeningState(ctx, ComponentName(ctx, HuntTile::class.java))
}
