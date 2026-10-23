package com.pokehunt

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.atomic.AtomicReference

/** The hunt's link to the website (poopihunt.xyz): asks for the login code
 *  when the hunt starts, sends what the hunter shows there, and takes the
 *  page's commands. The phone makes every connection: commands come back on
 *  a request held open by the server (/api/device/poll).
 *  key: this phone's secret, made once (Hunt.deviceKey). */
class Remote(private val key: String, private val label: String, private val build: String,
             private val onCommand: (JSONObject) -> Unit) : Link {
    companion object {
        const val SITE = "https://poopihunt.xyz"
    }

    /** The code to type on the website; empty until the server gives one
     *  (and once it's spent: a code works once). */
    @Volatile var code = ""
        private set
    /** A browser logged in with the last code. */
    @Volatile var loggedIn = false
        private set
    @Volatile private var stopped = false
    private val state = AtomicReference<JSONObject?>()
    private val shot = AtomicReference<ByteArray?>()
    private val sprite = AtomicReference<JSONObject?>()
    private val lock = Object()

    fun start() {
        Thread({ connect() }, "website").apply { isDaemon = true }.start()
        Thread({ send() }, "website-send").apply { isDaemon = true }.start()
    }

    fun stop() {
        stopped = true
        synchronized(lock) { lock.notifyAll() }
        Thread { runCatching { call("POST", "/api/device/stop", JSONObject()) } }.start()
    }

    override fun state(s: JSONObject) {
        state.set(s)
        synchronized(lock) { lock.notifyAll() }
    }

    override fun shot(jpg: ByteArray) {
        shot.set(jpg)
        synchronized(lock) { lock.notifyAll() }
    }

    override fun sprite(s: JSONObject) {
        sprite.set(s)
        synchronized(lock) { lock.notifyAll() }
    }

    /** A code, then commands until stopped. Offline: tries again. */
    private fun connect() {
        var wait = 2000L
        while (!stopped && code.isEmpty()) {
            try {
                val r = call("POST", "/api/device/start", JSONObject().put("label", label).put("kind", "android").put("build", build))
                code = r.getString("code")
                Log.print("  (connected to the website: its code is in the Hunt app)")
            } catch (e: Exception) {
                Thread.sleep(wait)
                wait = minOf(wait * 2, 60_000)
            }
        }
        wait = 2000L
        while (!stopped) {
            try {
                val r = call("GET", "/api/device/poll", null, readTimeout = 40_000)
                val cmds = r.optJSONArray("commands")
                for (i in 0 until (cmds?.length() ?: 0)) {
                    val cmd = cmds!!.getJSONObject(i)
                    if (cmd.optString("cmd") == "login") {
                        loggedIn = true
                        code = ""  // spent
                    }
                    if (!stopped) onCommand(cmd)
                }
                wait = 2000L
            } catch (e: Exception) {
                if (stopped) break
                Thread.sleep(wait)
                wait = minOf(wait * 2, 30_000)
            }
        }
    }

    /** A new code; the old one stops working. Network: not on the main thread. */
    fun newCode() {
        code = call("POST", "/api/device/code", JSONObject()).getString("code")
        loggedIn = false
    }

    /** Logs every browser out of this phone. Network: not on the main thread. */
    fun logOutBrowsers() {
        call("POST", "/api/device/logout", JSONObject())
        loggedIn = false
    }

    /** The newest state, sprite and picture, as they come; one at a time, older ones skipped. */
    private fun send() {
        var last: JSONObject? = null
        var sentAt = 0L
        while (!stopped) {
            // the last state again every 20 s: a restarted server knows it again
            val s = state.getAndSet(null) ?: last?.takeIf { System.currentTimeMillis() - sentAt > 20_000 }
            val jpg = shot.getAndSet(null)
            val sp = sprite.getAndSet(null)
            if (s == null && jpg == null && sp == null) {
                synchronized(lock) { lock.wait(5000) }
                continue
            }
            try {
                if (sp != null) call("POST", "/api/device/sprite", sp)
                if (jpg != null) call("POST", "/api/device/shot", null, jpg)
                if (s != null) {
                    call("POST", "/api/device/state", s)
                    last = s
                    sentAt = System.currentTimeMillis()
                }
            } catch (e: Exception) {
                // offline: the next state replaces this one
                Thread.sleep(2000)
            }
        }
    }

    private fun call(method: String, path: String, json: JSONObject?, jpg: ByteArray? = null,
                     readTimeout: Int = 15_000): JSONObject {
        val c = URL(SITE + path).openConnection() as HttpURLConnection
        try {
            c.requestMethod = method
            c.connectTimeout = 15_000
            c.readTimeout = readTimeout
            c.useCaches = false
            c.setRequestProperty("Authorization", "Bearer $key")
            c.setRequestProperty("User-Agent", "Hunt (android, $build)")
            val body = jpg ?: json?.toString()?.toByteArray()
            if (body != null) {
                c.doOutput = true
                c.setRequestProperty("Content-Type", if (jpg != null) "image/jpeg" else "application/json")
                c.setFixedLengthStreamingMode(body.size)
                c.outputStream.use { it.write(body) }
            }
            val code = c.responseCode
            if (code !in 200..299) throw RuntimeException("HTTP $code")
            val text = c.inputStream.bufferedReader().readText()
            return if (text.isBlank()) JSONObject() else JSONObject(text)
        } finally {
            c.disconnect()
        }
    }
}
