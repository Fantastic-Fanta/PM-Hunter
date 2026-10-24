package com.pokehunt

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.pm.PackageInstaller
import android.os.Build
import android.content.Intent
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/** Updates from the web: version.json next to the APK says which build is the
 *  newest, {"versionCode": 878152, "versionName": "22 Oct 13:12", "apk": "Hunt.apk"}.
 *  Looked for when the hunt stops (HuntService), installed on its own. */
object Updater {
    const val URL = "https://poopihunt.xyz/version.json"  // hunt.stingray-digital.online serves the same, for older builds

    class Update(val code: Long, val name: String, val apk: String)

    private fun installed(ctx: Context) = ctx.packageManager.getPackageInfo(ctx.packageName, 0).longVersionCode

    /** A newer build than this one, or null. Network: not on the main thread. */
    fun check(ctx: Context): Update? = try {
        val c = URL(URL).openConnection() as HttpURLConnection
        c.connectTimeout = 10000
        c.readTimeout = 10000
        c.useCaches = false
        val o = JSONObject(c.inputStream.bufferedReader().readText())
        c.disconnect()
        val code = o.getLong("versionCode")
        if (code > installed(ctx))
            Update(code, o.optString("versionName"), java.net.URL(java.net.URL(URL), o.optString("apk", "Hunt.apk")).toString())
        else null
    } catch (e: Exception) {
        null  // offline, or nothing published: no update
    }

    /** Downloads it and installs it (not on the main thread), on its own: Android
     *  updates an app it installed itself without asking (12+), so only the first
     *  update from the website may show Android's confirm screen. progress: 0 to 100. */
    fun install(ctx: Context, update: Update, progress: (Int) -> Unit) {
        val file = File(ctx.cacheDir, "update.apk")
        val c = URL(update.apk).openConnection() as HttpURLConnection
        c.connectTimeout = 15000
        c.readTimeout = 30000
        val total = c.contentLengthLong
        c.inputStream.use { input ->
            file.outputStream().use { out ->
                val buf = ByteArray(64 * 1024)
                var done = 0L
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    out.write(buf, 0, n)
                    done += n
                    if (total > 0) progress((done * 100 / total).toInt())
                }
            }
        }
        c.disconnect()
        val installer = ctx.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(ctx.packageName)
            if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
        }
        val id = installer.createSession(params)
        installer.openSession(id).use { session ->
            session.openWrite("hunt.apk", 0, file.length()).use { out -> file.inputStream().use { it.copyTo(out) } }
            val done = PendingIntent.getBroadcast(ctx, id, Intent(ctx, UpdateDone::class.java),
                PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            session.commit(done.intentSender)  // the app's replaced and started again from here
        }
    }
}

/** Android's word on an install: if it wants you to confirm (the first time, or before
 *  Android 12), its screen comes up. */
class UpdateDone : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
                ctx.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            PackageInstaller.STATUS_SUCCESS -> Log.print("  (updated)")
            else -> Log.print("  (the update didn't install: ${intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)})")
        }
    }
}
