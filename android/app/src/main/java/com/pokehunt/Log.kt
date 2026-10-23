package com.pokehunt

import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** What the hunter says: shown on the app's screen and kept in hunt.log. */
object Log {
    var file: File? = null
    private val lines = ArrayDeque<String>()
    private var lastSaid: String? = null
    /** Also gets every line (the hunter sends them to the debug webhook). */
    @Volatile var sink: ((String) -> Unit)? = null

    @Synchronized
    fun print(msg: String) {
        for (line in msg.trimStart('\n').lines()) {
            lines.addLast(line)
            while (lines.size > 300) lines.removeFirst()
            sink?.invoke(line)
        }
        file?.let { runCatching { it.appendText(msg.trimStart('\n') + "\n") } }
        android.util.Log.i("pokehunt", msg)
    }

    /** Print, but don't repeat the same waiting message every loop. */
    @Synchronized
    fun say(msg: String) {
        if (msg != lastSaid) {
            print(msg)
            lastSaid = msg
        }
    }

    @Synchronized
    fun resetSay() { lastSaid = null }

    @Synchronized
    fun recent(): String = lines.joinToString("\n")

    /** The latest line said, for labelling a screenshot. */
    @Synchronized
    fun last(): String = lines.lastOrNull { it.isNotBlank() } ?: ""

    fun error(e: Throwable) {
        file?.let { runCatching { it.appendText("${Date()}\n${e.stackTraceToString()}\n") } }
    }

    fun clock(): String = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date())
}
