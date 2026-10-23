package com.pokehunt

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/** Updates from the web: version.json next to the APK says which build is the
 *  newest, {"versionCode": 878152, "versionName": "22 Oct 13:12", "apk": "Hunt.apk"}.
 *  Android asks before installing; apps can't update themselves silently. */
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

    /** Downloads it (not on the main thread), then opens Android's installer.
     *  The first time, Android sends you to allow installing from Hunt. */
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
        if (!ctx.packageManager.canRequestPackageInstalls()) {
            ctx.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${ctx.packageName}"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return  // tapping Update again installs it
        }
        val uri = FileProvider.getUriForFile(ctx, "${ctx.packageName}.files", file)
        ctx.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
