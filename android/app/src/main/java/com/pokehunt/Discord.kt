package com.pokehunt

import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID

/** Posts to the webhook in the background, one message at a time and in
 *  order; failures are logged, not fatal. Slows down when Discord says so
 *  (about 30 messages a minute per webhook).
 *  quiet: @here and the like don't ping (the debug channel). */
class Discord(private val url: String, private val quiet: Boolean = false) {
    private val queue = java.util.concurrent.LinkedBlockingQueue<Pair<String, List<Pair<String, ByteArray>>>>()

    init {
        if (url.isNotEmpty()) Thread {
            while (true) {
                val (text, files) = queue.take()
                send(text, files)
            }
        }.apply { isDaemon = true }.start()
    }

    fun post(text: String, png: ByteArray? = null) =
        postFiles(text, if (png == null) emptyList() else listOf("encounter.png" to png))

    /** A message with up to 10 pictures (name to bytes, png or jpg). */
    fun postFiles(text: String, files: List<Pair<String, ByteArray>>) {
        if (url.isNotEmpty()) queue.put(text to files.take(10))
    }

    private fun send(text: String, files: List<Pair<String, ByteArray>>) {
        val boundary = UUID.randomUUID().toString().replace("-", "")
        val payload = JSONObject().put("content", text.take(2000))
            .put("allowed_mentions", JSONObject().put("parse",
                JSONArray(if (quiet) emptyList() else listOf("everyone", "users", "roles"))))
        val body = ByteArrayOutputStream()
        fun part(disposition: String, type: String, data: ByteArray) {
            body.write("--$boundary\r\nContent-Disposition: form-data; $disposition\r\nContent-Type: $type\r\n\r\n".toByteArray())
            body.write(data)
            body.write("\r\n".toByteArray())
        }
        part("name=\"payload_json\"", "application/json", payload.toString().toByteArray())
        files.forEachIndexed { i, (name, data) ->
            part("name=\"files[$i]\"; filename=\"$name\"", if (name.endsWith(".jpg")) "image/jpeg" else "image/png", data)
        }
        body.write("--$boundary--\r\n".toByteArray())
        for (attempt in 0 until 6) {  // the network drops a request now and then
            try {
                val c = URL(url).openConnection() as HttpURLConnection
                c.requestMethod = "POST"
                c.doOutput = true
                c.connectTimeout = 20000
                c.readTimeout = 30000
                c.setRequestProperty("Content-Type", "multipart/form-data; boundary=$boundary")
                c.setRequestProperty("User-Agent", "pokehunt (android, 1.0)")
                c.outputStream.use { it.write(body.toByteArray()) }
                val code = c.responseCode
                if (code == 429) {  // too fast: wait as long as Discord says
                    val wait = c.getHeaderField("Retry-After")?.toDoubleOrNull() ?: 2.0
                    c.disconnect()
                    Thread.sleep((wait * 1000).toLong() + 250)
                    continue
                }
                c.disconnect()
                if (code in 200..299) return
                throw RuntimeException("HTTP $code")
            } catch (e: Exception) {
                if (attempt == 5) Log.print("  ! Discord message failed: ${e.message}")
                else Thread.sleep(3000L * (attempt + 1))
            }
        }
    }
}
