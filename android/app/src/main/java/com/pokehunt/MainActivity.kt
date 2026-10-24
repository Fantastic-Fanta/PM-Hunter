package com.pokehunt

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.WindowInsets
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.FileProvider
import java.io.File

/** One big Start / Stop button. What to hunt and the rest are folded away
 *  underneath. */
class MainActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private val CAPTURE = 7
    private var starting = false

    private lateinit var dot: TextView
    private lateinit var status: TextView
    private lateinit var detail: TextView
    private lateinit var website: TextView
    private lateinit var webCard: LinearLayout
    private lateinit var main: TextView
    private lateinit var update: TextView
    private var checkedAt = 0L
    private lateinit var log: TextView
    private lateinit var config: EditText
    private lateinit var editor: LinearLayout

    // black, white and grays only
    private val BG = 0xFF0B0B0B.toInt()
    private val CARD = 0xFF1A1A1A.toInt()
    private val TEXT = 0xFFFFFFFF.toInt()
    private val DIM = 0xFF9E9E9E.toInt()
    private val INK = 0xFF000000.toInt()
    private val CLEAR = 0x00000000
    private val RADIUS = 4  // dp, every corner

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun shape(fill: Int, outline: Int? = null) = GradientDrawable().apply {
        setColor(fill)
        cornerRadius = dp(RADIUS).toFloat()
        outline?.let { setStroke(dp(2), it) }
    }

    private fun pressable(fill: Int, outline: Int? = null) =
        RippleDrawable(ColorStateList.valueOf(0x40808080), shape(fill, outline), null)

    private fun text(s: String, size: Float, color: Int = TEXT, bold: Boolean = false) = TextView(this).apply {
        text = s
        textSize = size
        setTextColor(color)
        if (bold) typeface = Typeface.DEFAULT_BOLD
    }

    private fun gap(h: Int) = View(this).apply { layoutParams = LinearLayout.LayoutParams(1, dp(h)) }

    private fun button(s: String, fill: Int, ink: Int, f: () -> Unit) = text(s, 16f, ink, true).apply {
        gravity = Gravity.CENTER
        background = pressable(fill)
        setPadding(dp(16), dp(14), dp(16), dp(14))
        setOnClickListener { f() }
    }

    /** A header that folds its content open and shut. */
    private fun fold(col: LinearLayout, title: String, content: LinearLayout) {
        content.visibility = View.GONE
        val head = text("▸  $title", 17f, TEXT, true).apply {
            setPadding(dp(4), dp(14), dp(4), dp(14))
            setOnClickListener {
                val open = content.visibility != View.VISIBLE
                content.visibility = if (open) View.VISIBLE else View.GONE
                text = (if (open) "▾  " else "▸  ") + title
            }
        }
        col.addView(head)
        col.addView(content)
    }

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        Log.file = File(filesDir, "hunt.log")
        if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)

        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(24), dp(20), dp(24))
        }
        // status
        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = shape(CARD)
            setPadding(dp(20), dp(18), dp(20), dp(18))
        }
        val line = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        dot = text("●", 22f)
        status = text("", 22f, TEXT, true).apply { setPadding(dp(10), 0, 0, 0) }
        line.addView(dot)
        line.addView(status)
        detail = text("", 16f, DIM).apply { setPadding(0, dp(6), 0, 0) }
        card.addView(line)
        card.addView(detail)
        col.addView(card)
        // the code for the website, while hunting
        webCard = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = shape(CARD)
            setPadding(dp(20), dp(14), dp(20), dp(14))
            visibility = View.GONE
        }
        website = text("", 16f, TEXT)
        webCard.addView(website)
        val webButtons = LinearLayout(this).apply { setPadding(0, dp(12), 0, 0) }
        for ((label, f) in listOf<Pair<String, (com.pokehunt.core.Hunter) -> Boolean>>(
            "New code" to { it.newCode() },
            "Log out browsers" to { it.logOutBrowsers() },
        )) {
            webButtons.addView(button(label, BG, TEXT) {
                val hunter = HuntService.instance?.hunter ?: return@button
                Thread {
                    val ok = runCatching { f(hunter) }.getOrDefault(false)
                    runOnUiThread {
                        Toast.makeText(this, if (!ok) "No connection to poopihunt.xyz" else if (label == "New code")
                            "New code: the old one won't work" else "Every browser is logged out", Toast.LENGTH_SHORT).show()
                        refresh.run()
                    }
                }.start()
            }, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f).apply { if (label == "New code") marginEnd = dp(8) })
        }
        webCard.addView(webButtons)
        col.addView(gap(10))
        col.addView(webCard, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        col.addView(gap(16))

        // the button
        main = text("", 24f, TEXT, true).apply {
            gravity = Gravity.CENTER
            minHeight = dp(76)
            setOnClickListener { onMain() }
        }
        col.addView(main, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        // shows only when a newer build is published
        update = button("", CLEAR, TEXT) {}.apply {
            background = pressable(CLEAR, TEXT)
            visibility = View.GONE
        }
        col.addView(gap(10))
        col.addView(update, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        col.addView(gap(10))
        col.addView(gap(16))

        // what it's doing
        log = text("", 14f, TEXT).apply {
            typeface = Typeface.MONOSPACE
            background = shape(CARD)
            setPadding(dp(16), dp(14), dp(16), dp(14))
        }
        col.addView(log)
        col.addView(gap(12))

        // what to hunt
        editor = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        config = EditText(this).apply {
            setText(Hunt.configFile(this@MainActivity).readText())
            typeface = Typeface.MONOSPACE
            textSize = 13f
            setTextColor(TEXT)
            background = shape(CARD)
            setPadding(dp(12), dp(12), dp(12), dp(12))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or
                InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            setHorizontallyScrolling(true)
            minLines = 10
            maxLines = 18
            gravity = Gravity.TOP
        }
        editor.addView(config)
        editor.addView(gap(8))
        editor.addView(button("Save", TEXT, INK) { save() })
        fold(col, "What to hunt", editor)

        // the rest
        val more = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        more.addView(Switch(this).apply {
            text = "Watch only"
            textSize = 16f
            setTextColor(TEXT)
            isChecked = Hunt.watchOnly(this@MainActivity)
            setPadding(0, dp(8), 0, dp(8))
            setOnCheckedChangeListener { _, on -> Hunt.prefs(this@MainActivity).edit().putBoolean("watch", on).apply() }
        })
        more.addView(Switch(this).apply {
            text = "Debug mode"  // catches every encounter, then saves; screenshots each catch turn
            textSize = 16f
            setTextColor(TEXT)
            isChecked = Hunt.debugMode(this@MainActivity)
            setPadding(0, dp(8), 0, dp(8))
            setOnCheckedChangeListener { _, on ->
                Hunt.prefs(this@MainActivity).edit().putBoolean("debug", on).apply()
                if (Hunt.running) Toast.makeText(this@MainActivity, "Restart to use it", Toast.LENGTH_SHORT).show()
            }
        })
        for ((label, f) in listOf<Pair<String, () -> Unit>>(
            "Calibrate" to { calibrate() },
            "Reset calibration" to {
                HuntService.instance?.forgetCalibration()
                Toast.makeText(this, "Reset", Toast.LENGTH_SHORT).show()
            },
            "Share encounters" to { share("encounters.csv", "text/csv") },
            "Share log" to { share("hunt.log", "text/plain") },
            "Accessibility" to { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) },
        )) {
            more.addView(gap(8))
            more.addView(button(label, CARD, TEXT, f))
        }
        fold(col, "More", more)
        col.addView(gap(24))
        col.addView(text("built ${packageManager.getPackageInfo(packageName, 0).versionName}", 12f, DIM).apply {
            gravity = Gravity.CENTER
        })

        setContentView(ScrollView(this).apply {
            setBackgroundColor(BG)
            addView(col)
            // Android 15+ draws apps under the status and navigation bars
            setOnApplyWindowInsetsListener { v, insets ->
                val bars = insets.getInsets(WindowInsets.Type.systemBars())
                v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
                insets
            }
        })
        handle(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    /** Sent here by the Quick Settings tile when it couldn't start alone. */
    private fun handle(intent: Intent?) {
        when (val problem = intent?.getStringExtra("start") ?: return) {
            "service" -> {}  // the screen says what to do
            "capture" -> askCapture()
            else -> configProblem(problem)
        }
        intent?.removeExtra("start")
    }

    private fun onMain() {
        if (HuntService.instance == null) {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            return
        }
        if (Hunt.running) {
            Log.print("  (Stop tapped in the app)")
            Hunt.stop()
            refresh.run()
            return
        }
        if (starting) return
        starting = true
        refresh.run()
        Thread {
            val problem = Hunt.start(this)
            runOnUiThread {
                starting = false
                when (problem) {
                    null -> Hunt.refreshTile(this)
                    "service" -> startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                    "capture" -> askCapture()
                    else -> configProblem(problem)
                }
                refresh.run()
            }
        }.start()
    }

    private fun configProblem(problem: String) {
        Toast.makeText(this, problem, Toast.LENGTH_LONG).show()
        editor.visibility = View.VISIBLE
    }

    /** Some Androids (emulators especially) give blank screenshots: then the
     *  screen is watched through screen capture, which Android asks about. */
    private fun askCapture() {
        @Suppress("DEPRECATION")
        startActivityForResult(getSystemService(MediaProjectionManager::class.java).createScreenCaptureIntent(), CAPTURE)
    }

    @Deprecated("startActivityForResult is all a plain Activity has")
    override fun onActivityResult(request: Int, result: Int, data: Intent?) {
        super.onActivityResult(request, result, data)
        if (request != CAPTURE) return
        if (result != RESULT_OK || data == null) {
            Toast.makeText(this, "Hunt can't see this screen without screen capture", Toast.LENGTH_LONG).show()
            return
        }
        startForegroundService(Intent(this, CaptureService::class.java).putExtra("code", result).putExtra("data", data))
        val since = System.currentTimeMillis()
        ui.post(object : Runnable {
            override fun run() {  // start once the capture is up
                if (CaptureService.instance != null) onMain()
                else if (System.currentTimeMillis() - since < 5000) ui.postDelayed(this, 100)
            }
        })
    }

    private fun save() {
        val text = config.text.toString()
        val mistake = com.pokehunt.core.checkConfig(text)
        if (mistake != null) {
            Toast.makeText(this, "Not saved: $mistake", Toast.LENGTH_LONG).show()
            return
        }
        Hunt.configFile(this).writeText(text)
        Toast.makeText(this, if (Hunt.running) "Saved. Restart to use it" else "Saved", Toast.LENGTH_SHORT).show()
    }

    private fun calibrate() {
        val h = HuntService.instance?.hunter
        if (h == null) Toast.makeText(this, "Start hunting first", Toast.LENGTH_SHORT).show()
        else {
            h.calibrateNext()
            Toast.makeText(this, "Next battle. Picture in Pictures/Hunt", Toast.LENGTH_LONG).show()
        }
    }

    private fun share(name: String, type: String) {
        val file = File(filesDir, name)
        if (!file.exists()) {
            Toast.makeText(this, "Nothing logged yet", Toast.LENGTH_SHORT).show()
            return
        }
        val uri = FileProvider.getUriForFile(this, "$packageName.files", file)
        startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply {
            setType(type)
            putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }, name))
    }

    private val refresh = object : Runnable {
        override fun run() {
            val service = HuntService.instance
            val hunter = service?.hunter
            val (mark, title) = when {
                service == null -> "○" to "Not set up"
                starting -> "◌" to "Starting"
                hunter == null -> "○" to "Ready"
                hunter.running() -> "●" to "Hunting"
                else -> "◐" to "Off"
            }
            dot.text = mark
            status.text = title
            // the one thing that isn't obvious
            detail.text = if (service == null)
                "Greyed out? Settings › Apps › Hunt › ⋮ › Allow restricted settings" else ""
            detail.visibility = if (detail.text.isEmpty()) View.GONE else View.VISIBLE
            val remote = hunter?.takeIf { it.linked() }
            val code = remote?.code() ?: ""
            website.text = when {
                remote == null -> ""
                code.isNotEmpty() -> android.text.SpannableStringBuilder("Website code\n").append(code,
                    android.text.style.RelativeSizeSpan(2f), 0).append("\n").append("poopihunt.xyz/app",
                    android.text.style.ForegroundColorSpan(DIM), 0)
                remote.loggedIn() -> "A browser logged in on poopihunt.xyz with the code, so it's spent. " +
                    "New code gives another."
                else -> "Connecting to poopihunt.xyz…"
            }
            webCard.visibility = if (website.text.isEmpty()) View.GONE else View.VISIBLE
            // white for go, an outline for Stop
            main.text = when {
                service == null -> "Turn on"
                hunter != null -> "Stop"
                starting -> "…"
                else -> "Start"
            }
            main.background = when {
                hunter != null -> pressable(CLEAR, TEXT)
                starting -> pressable(CARD)
                else -> pressable(TEXT)
            }
            main.setTextColor(if (hunter != null || starting) TEXT else INK)
            val lines = Log.recent().lines().filter { it.isNotBlank() && !it.startsWith("  wanted:") &&
                !it.startsWith("  auto-catch:") && !it.startsWith("  post only:") }
            log.text = lines.takeLast(6).joinToString("\n")
            log.visibility = if (log.text.isEmpty()) View.GONE else View.VISIBLE
            ui.removeCallbacks(this)
            ui.postDelayed(this, 1000)
        }
    }

    override fun onResume() {
        super.onResume()
        refresh.run()
        checkForUpdate()
    }

    private fun checkForUpdate() {
        if (System.currentTimeMillis() - checkedAt < 10 * 60 * 1000) return
        checkedAt = System.currentTimeMillis()
        Thread {
            val u = Updater.check(this) ?: return@Thread
            runOnUiThread {
                update.text = "Update to the ${u.name} build"
                update.visibility = View.VISIBLE
                update.setOnClickListener {
                    update.isEnabled = false
                    Thread {
                        try {
                            Updater.install(this, u) { p -> runOnUiThread { update.text = "Downloading $p%" } }
                            runOnUiThread { update.text = "Update to the ${u.name} build" }
                        } catch (e: Exception) {
                            runOnUiThread { update.text = "Download failed, tap to retry" }
                        }
                        runOnUiThread { update.isEnabled = true }
                    }.start()
                }
            }
        }.start()
    }

    override fun onPause() {
        ui.removeCallbacks(refresh)
        super.onPause()
    }
}
