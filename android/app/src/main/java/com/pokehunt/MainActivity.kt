package com.pokehunt

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
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

/** Setting up, and nothing else: what Poopihunt needs allowed, each a row that opens
 *  where it's allowed, the Discord webhook, then Open Roblox. The hunt itself is the
 *  pill floating over Roblox (HuntService), which also keeps it up to date. */
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
            hint = "Discord webhook (for finds)"
            isSingleLine = true
            imeOptions = android.view.inputmethod.EditorInfo.IME_ACTION_DONE
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI
            background = GradientDrawable().apply { setColor(BOXED); cornerRadius = dp(28).toFloat() }
            setPadding(dp(22), dp(16), dp(22), dp(16))
            setText(Hunt.webhook(this@MainActivity))
            setOnEditorActionListener { v, _, _ ->
                val said = Hunt.saveWebhook(this@MainActivity, v.text.toString().trim())
                if (said != null) Toast.makeText(this@MainActivity, said, Toast.LENGTH_SHORT).show()
                if (said == "Not a Discord webhook") setText(Hunt.webhook(this@MainActivity))
                clearFocus()
                getSystemService(android.view.inputmethod.InputMethodManager::class.java).hideSoftInputFromWindow(windowToken, 0)
                true
            }
        }
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun words(s: String, size: Float, color: Int, medium: Boolean = false) = TextView(this).apply {
        text = s
        typeface = resources.getFont(if (medium) R.font.inter_medium else R.font.inter_regular)
        setTextSize(TypedValue.COMPLEX_UNIT_SP, size)
        setTextColor(color)
    }

    private fun pill(fill: Int) = RippleDrawable(ColorStateList.valueOf(0x40808080),
        GradientDrawable().apply { setColor(fill); cornerRadius = dp(28).toFloat() }, null)

    /** One thing to allow: its name, a word on the right ("On" or what to tap), and a
     *  line under it when that helps. */
    private fun row(name: String, done: Boolean, todo: String, tip: String? = null, f: () -> Unit) =
        LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val line = LinearLayout(this@MainActivity).apply {
                gravity = Gravity.CENTER_VERTICAL
                background = pill(BOXED)
                setPadding(dp(22), dp(16), dp(22), dp(16))
                addView(words(name, 16f, INK, true), LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
                addView(words(if (done) "On" else todo, 15f, if (done) DIM else INK))
                if (!done) setOnClickListener { f() }
            }
            addView(line, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
            if (tip != null && !done) addView(words(tip, 13f, DIM).apply { setPadding(dp(22), dp(8), dp(22), 0) })
        }

    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        Log.file = File(filesDir, "hunt.log")
        val title = LinearLayout(this).apply {
            gravity = Gravity.CENTER_VERTICAL
            addView(ImageView(this@MainActivity).apply {
                setImageResource(R.mipmap.ic_launcher)
                // a rounded square, as an app's icon
                outlineProvider = object : android.view.ViewOutlineProvider() {
                    override fun getOutline(v: View, o: android.graphics.Outline) =
                        o.setRoundRect(0, 0, v.width, v.height, v.width * 0.22f)
                }
                clipToOutline = true
                scaleType = ImageView.ScaleType.CENTER_CROP
            },
                    LinearLayout.LayoutParams(dp(44), dp(44)).apply { marginEnd = dp(14) })
            addView(words("Poopihunt", 26f, INK, true))
        }
        rows = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(title)
            addView(words("Allow these, then open Roblox: the pill floats over the game.", 15f, DIM).apply {
                setPadding(0, dp(10), 0, dp(24))
            })
            addView(rows, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        }
        val build = words(packageManager.getPackageInfo(packageName, 0).versionName ?: "", 12f, Color.rgb(0x55, 0x55, 0x55))
        val width = minOf(resources.displayMetrics.widthPixels - dp(40), dp(440))
        setContentView(FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            addView(col, FrameLayout.LayoutParams(width, WRAP_CONTENT, Gravity.CENTER))
            addView(build, FrameLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL)
                .apply { bottomMargin = dp(20) })
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
        val gap = { rows.addView(View(this), LinearLayout.LayoutParams(1, dp(12))) }
        rows.addView(row("Accessibility", HuntService.instance != null, "Turn on",
                         "Greyed out? Settings › Apps › Poopihunt › ⋮ › Allow restricted settings") {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        })
        if (Build.VERSION.SDK_INT >= 33) {
            gap()
            val on = checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
            rows.addView(row("Notifications", on, "Allow", "For finds, and browsers logging in") {
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
            })
        }
        // some Androids (emulators especially) give blank screenshots: then it watches the
        // screen through screen capture, asked for once a hunt found that out
        if (Hunt.prefs(this).getBoolean("needs_capture", false)) {
            gap()
            rows.addView(row("Screen capture", CaptureService.instance != null, "Allow",
                             "This phone's screenshots come out blank without it") { askCapture() })
        }
        gap()
        rows.addView(row("Install updates", packageManager.canRequestPackageInstalls(), "Allow",
                         "So it keeps itself up to date") {
            startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, android.net.Uri.parse("package:$packageName")))
        })
        gap()
        rows.addView(hookRow, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        rows.addView(View(this), LinearLayout.LayoutParams(1, dp(28)))
        rows.addView(words("Open Roblox", 17f, Color.BLACK, true).apply {
            gravity = Gravity.CENTER
            background = pill(INK)
            setPadding(dp(22), dp(18), dp(22), dp(18))
            setOnClickListener {
                val go = packageManager.getLaunchIntentForPackage(HuntService.ROBLOX)
                if (go == null) Toast.makeText(this@MainActivity, "Roblox isn't installed", Toast.LENGTH_SHORT).show()
                else startActivity(go)
            }
        }, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
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
        refresh.run()
    }

    override fun onPause() {
        ui.removeCallbacks(refresh)
        super.onPause()
    }
}
