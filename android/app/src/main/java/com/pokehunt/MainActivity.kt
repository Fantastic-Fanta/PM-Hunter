package com.pokehunt

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.content.res.ColorStateList
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.WindowInsets
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import java.io.File

/** Setting up, and nothing else: the title, a pill for each permission it needs (what
 *  it says, and a tap opens where it's allowed), and the Discord webhook's. The hunt
 *  itself is the pill floating over Roblox (HuntService), which keeps it up to date. */
class MainActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private val CAPTURE = 7

    private val INK = Color.WHITE
    private val DIM = Color.rgb(0x84, 0x84, 0x84)
    private val BOXED = Color.rgb(0x1d, 0x1d, 0x1d)

    private lateinit var rows: LinearLayout
    private val hookRow by lazy {
        android.widget.EditText(this).apply {
            typeface = resources.getFont(R.font.inter_regular)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f)
            setTextColor(INK)
            setHintTextColor(DIM)
            hint = "Paste Discord webhook here"
            isSingleLine = true
            imeOptions = android.view.inputmethod.EditorInfo.IME_ACTION_DONE
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI
            background = GradientDrawable().apply { setColor(BOXED); cornerRadius = dp(28).toFloat() }
            setPadding(dp(22), dp(16), dp(22), dp(16))
            setText(short(Hunt.webhook(this@MainActivity)))
            // the address in full only while it's being changed
            setOnFocusChangeListener { _, focused ->
                setText(if (focused) Hunt.webhook(this@MainActivity) else short(Hunt.webhook(this@MainActivity)))
                if (focused) setSelection(text.length)
            }
            setOnEditorActionListener { v, _, _ ->
                val said = Hunt.saveWebhook(this@MainActivity, v.text.toString().trim())
                if (said != null) Toast.makeText(this@MainActivity, said, Toast.LENGTH_SHORT).show()
                clearFocus()
                getSystemService(android.view.inputmethod.InputMethodManager::class.java).hideSoftInputFromWindow(windowToken, 0)
                true
            }
        }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    /** A webhook's address shortened: "discord.com/…/129834…4fQz". */
    private fun short(url: String): String {
        val m = Regex("^https://(?:ptb\\.|canary\\.)?(discord(?:app)?\\.com)/api/webhooks/(\\d+)/([\\w-]+)$").find(url)
            ?: return url
        val (host, id, token) = m.destructured
        return "$host/…/${id.take(6)}…${token.takeLast(4)}"
    }

    private fun words(s: String, size: Float, color: Int, medium: Boolean = false) = TextView(this).apply {
        text = s
        typeface = resources.getFont(if (medium) R.font.inter_medium else R.font.inter_regular)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, size)
        setTextColor(color)
    }

    private fun pill(fill: Int) = RippleDrawable(ColorStateList.valueOf(0x40808080),
        GradientDrawable().apply { setColor(fill); cornerRadius = dp(28).toFloat() }, null)

    /** One thing to allow: its name and a word on the right ("On", or what to tap). */
    private fun row(name: String, done: Boolean, todo: String, f: () -> Unit) =
        LinearLayout(this).apply {
            gravity = Gravity.CENTER_VERTICAL
            background = pill(BOXED)
            setPadding(dp(22), dp(16), dp(22), dp(16))
            addView(words(name, 16f, INK, true), LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
            addView(words(if (done) "On" else todo, 15f, if (done) DIM else INK))
            if (!done) setOnClickListener { f() }
        }

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        Log.file = File(filesDir, "hunt.log")
        // notifications (finds, browsers logging in): Android's own question, the first time
        if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        val title = LinearLayout(this).apply {
            gravity = Gravity.CENTER
            // Clodsire, white, as on the icon
            addView(ImageView(this@MainActivity).apply {
                setImageResource(R.drawable.clodsire_mark)
                scaleType = ImageView.ScaleType.FIT_CENTER
                adjustViewBounds = true
            }, LinearLayout.LayoutParams(dp(56), dp(28)).apply { marginEnd = dp(14) })
            addView(words("Poopihunt", 26f, INK, true))
        }
        rows = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(title, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
            addView(View(this@MainActivity), LinearLayout.LayoutParams(1, dp(24)))
            addView(rows, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        }
        val width = minOf(resources.displayMetrics.widthPixels - dp(40), dp(440))
        setContentView(FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            addView(col, FrameLayout.LayoutParams(width, WRAP_CONTENT, Gravity.CENTER))
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

    /** Sent here by the pill or the Quick Settings tile when a hunt couldn't start. */
    private fun handle(intent: Intent?) {
        if (intent?.getBooleanExtra("logout", false) == true) {  // the browser-login notification
            intent.removeExtra("logout")
            val h = HuntService.instance?.hunter
            Thread {
                val ok = h != null && runCatching { h.logOutBrowsers() }.getOrDefault(false)
                runOnUiThread { Toast.makeText(this, if (ok) "Every browser is logged out" else "No connection to poopihunt.xyz",
                                               Toast.LENGTH_LONG).show() }
            }.start()
            return
        }
        when (val problem = intent?.getStringExtra("start") ?: return) {
            "service" -> {}  // its row says what to do
            "capture" -> {
                Hunt.prefs(this).edit().putBoolean("needs_capture", true).apply()
                askCapture()
            }
            else -> Toast.makeText(this, problem, Toast.LENGTH_LONG).show()
        }
        intent?.removeExtra("start")
    }

    private val refresh = object : Runnable {
        override fun run() {
            show()
            ui.removeCallbacks(this)
            ui.postDelayed(this, 1000)
        }
    }

    private fun show() {
        if (hookRow.hasFocus()) return  // (being typed in)
        rows.removeAllViews()
        // only what's still to allow (a pill goes once it's on), then the webhook
        if (HuntService.instance == null) {
            rows.addView(row("Accessibility", false, "Turn on") { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) })
            rows.addView(View(this), LinearLayout.LayoutParams(1, dp(12)))
        }
        // some Androids (emulators especially) give blank screenshots: then it watches the
        // screen through screen capture, asked for once a hunt found that out
        if (Hunt.prefs(this).getBoolean("needs_capture", false) && CaptureService.instance == null) {
            rows.addView(row("Screen capture", false, "Allow") { askCapture() })
            rows.addView(View(this), LinearLayout.LayoutParams(1, dp(12)))
        }
        rows.addView(hookRow, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
    }

    /** Screen capture: Android asks; once it's up the hunt starts, and Roblox comes up. */
    private fun askCapture() {
        @Suppress("DEPRECATION")
        startActivityForResult(getSystemService(MediaProjectionManager::class.java).createScreenCaptureIntent(), CAPTURE)
    }

    @Deprecated("startActivityForResult is all a plain Activity has")
    override fun onActivityResult(request: Int, result: Int, data: Intent?) {
        super.onActivityResult(request, result, data)
        if (request != CAPTURE) return
        if (result != RESULT_OK || data == null) {
            Toast.makeText(this, "Poopihunt can't see this screen without screen capture", Toast.LENGTH_LONG).show()
            return
        }
        startForegroundService(Intent(this, CaptureService::class.java).putExtra("code", result).putExtra("data", data))
        val since = System.currentTimeMillis()
        ui.post(object : Runnable {
            override fun run() {  // the hunt, once the capture is up
                if (CaptureService.instance != null) Thread {
                    val problem = Hunt.start(this@MainActivity)
                    runOnUiThread {
                        if (problem != null && problem != "capture") Toast.makeText(this@MainActivity, problem, Toast.LENGTH_LONG).show()
                        packageManager.getLaunchIntentForPackage(HuntService.ROBLOX)?.let { startActivity(it) }
                    }
                }.start()
                else if (System.currentTimeMillis() - since < 5000) ui.postDelayed(this, 100)
            }
        })
    }

    override fun onResume() {
        super.onResume()
        HuntService.instance?.appOpen(true)  // (no pill over this page)
        refresh.run()
    }

    override fun onPause() {
        ui.removeCallbacks(refresh)
        HuntService.instance?.appOpen(false)
        super.onPause()
    }
}
