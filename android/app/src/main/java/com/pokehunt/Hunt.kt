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

    val running get() = HuntService.instance?.hunter != null

    fun webhook(ctx: Context): String =
        runCatching { org.json.JSONObject(configFile(ctx).readText()).optString("webhook", "") }.getOrDefault("")

    /** The pill's webhook box: saved in config.json (a hunt that's on starts again with
     *  it). What to say about it, or null for nothing to save. */
    fun saveWebhook(ctx: Context, url: String): String? {
        if (url == webhook(ctx) || url == "Webhook on") return null
        if (url.isNotEmpty() && !Regex("^https://(ptb\\.|canary\\.)?discord(app)?\\.com/api/webhooks/\\d+/[\\w-]+$").matches(url))
            return "Not a Discord webhook"
        val file = configFile(ctx)
        val cfg = runCatching { org.json.JSONObject(file.readText()) }.getOrNull() ?: return "config.json has a mistake"
        cfg.put("webhook", url)
        file.writeText(cfg.toString(2))
        if (running) {  // the hunt reads it when it starts
            stop()
            Thread { start(ctx) }.start()
        }
        return if (url.isEmpty()) "Webhook off" else "Webhook saved"
    }

    /** Starts a hunt. Blocks for a moment (it checks that screenshots work),
     *  so not on the main thread. Null once started, else what's missing:
     *  "service" (off in Accessibility), "capture" (needs screen capture
     *  allowed first) or the config's problem. */
    fun start(ctx: Context): String? {
        val service = HuntService.instance ?: return "service"
        val cfg = configFile(ctx).readText()
        com.pokehunt.core.checkConfig(cfg)?.let { return "What to hunt has a mistake: $it" }
        if (CaptureService.instance == null && !service.screenshotsWork()) return "capture"
        service.startHunt(cfg, watch = false)  // (a debug run is play's long press, not a mode)
        return null
    }

    fun stop() {
        HuntService.instance?.stopHunt()
    }

    /** The tile shows whether a hunt is on. */
    fun refreshTile(ctx: Context) =
        TileService.requestListeningState(ctx, ComponentName(ctx, HuntTile::class.java))
}
