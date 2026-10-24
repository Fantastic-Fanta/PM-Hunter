package com.pokehunt

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.ContentValues
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.media.AudioManager
import android.media.RingtoneManager
import android.media.ToneGenerator
import android.os.Bundle
import android.os.Environment
import android.os.VibrationEffect
import android.os.Vibrator
import android.provider.MediaStore
import android.view.Display
import android.view.Gravity
import android.view.MotionEvent
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.TextView
import androidx.core.app.NotificationCompat
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import com.pokehunt.core.Hunter
import com.pokehunt.core.Phone
import com.pokehunt.core.Rect
import com.pokehunt.core.Shot
import java.io.File
import java.nio.ByteBuffer
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt

fun now() = System.nanoTime() / 1e9

/** The hunter's hands and eyes on the phone: an accessibility service, which
 *  may take screenshots (Android 11+) and tap for you. It also shows the
 *  pause button floating over Roblox, and notices you touching the screen.
 *  The hunter itself is Rust (libhunt.so, the same as on Mac and Windows),
 *  which reaches the phone through Phone. */
class HuntService : AccessibilityService(), Phone {
    companion object {
        const val ROBLOX = "com.roblox.client"
        @Volatile var instance: HuntService? = null
            private set
    }

    /** The hunt that's on; its website code shows in the app. */
    @Volatile var hunter: Hunter? = null
        private set
    private var thread: Thread? = null
    private val shots = Executors.newSingleThreadExecutor()
    private val recognizer by lazy { TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS) }
    private var lastShot = 0L
    @Volatile private var tapping = false
    @Volatile private var tappedAt = 0.0
    @Volatile private var touched = 0.0
    private var button: TextView? = null

    override fun onServiceConnected() {
        instance = this
        Log.file = File(filesDir, "hunt.log")
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel("finds", "Wanted Pokemon", NotificationManager.IMPORTANCE_HIGH))
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel("website", "Website logins", NotificationManager.IMPORTANCE_HIGH))
        robloxShown = rootInActiveWindow?.packageName == ROBLOX
        updateButton()
    }

    override fun typeText(text: String): Boolean = runCatching {
        val field = rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: return@runCatching false
        if (!field.isEditable) return@runCatching false
        val args = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text) }
        field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
    }.getOrDefault(false)

    override fun keyboardHidden(hidden: Boolean) {
        softKeyboardController.showMode = if (hidden) SHOW_MODE_HIDDEN else SHOW_MODE_AUTO
    }

    override fun onDestroy() {
        stopHunt()
        instance = null
        super.onDestroy()
    }

    /** Whether Roblox is the app on screen: the ON / OFF button shows over it. */
    @Volatile private var robloxShown = false

    private var inFront = ""  // what was last noticed coming over the screen, for the log

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event?.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return
        val pkg = event.packageName?.toString() ?: return
        // what can interrupt a hunt: another app, the notification shade, the keyboard
        if (hunter != null && pkg != packageName && pkg != inFront) {
            inFront = pkg
            Log.print("  (" + when {
                pkg == ROBLOX -> "Roblox is in front"
                pkg == "com.android.systemui" -> "Android's notification shade or a system dialog is over Roblox"
                pkg in keyboards() -> "the keyboard is up"
                else -> "${appName(pkg)} is in front of Roblox"
            } + ")")
        }
        // the notification shade, a keyboard or this app's own button don't count
        if (pkg == packageName || pkg == "com.android.systemui" || pkg in keyboards()) return
        robloxShown = pkg == ROBLOX
        updateButton()
    }

    private fun appName(pkg: String) = runCatching {
        packageManager.getApplicationLabel(packageManager.getApplicationInfo(pkg, 0)).toString()
    }.getOrDefault(pkg)

    private fun keyboards() = getSystemService(android.view.inputmethod.InputMethodManager::class.java)
        .enabledInputMethodList.map { it.packageName }
    override fun onInterrupt() {}

    // ------------------------------------------------------------ start / stop

    /** A hunt with config.json's text (checked already: checkConfig). */
    fun startHunt(config: String, watch: Boolean, debug: Boolean = false) {
        endHunter()
        val build = packageManager.getPackageInfo(packageName, 0).versionName ?: ""
        // watching never taps, so there's nothing to take over on the website
        val h = Hunter(this, config, filesDir.path, watch, debug, Hunt.deviceKey(this), build, BuildConfig.DEBUG_WEBHOOK)
        hunter = h
        thread = Thread({
            h.run()
            if (hunter === h) main { endHunter() }  // (it stopped by itself)
        }, "hunter").apply { start() }
        updateButton()
        Hunt.refreshTile(this)
    }

    fun stopHunt() {
        stopService(Intent(this, CaptureService::class.java))
        endHunter()
    }

    private fun endHunter() {
        hunter?.stop()
        hunter = null
        thread = null
        updateButton()
        Hunt.refreshTile(this)
    }

    private fun main(f: () -> Unit) = android.os.Handler(mainLooper).post(f)

    /** Someone logged in with this phone's code. */
    override fun websiteLogin() {
        val title = "A browser logged in to Hunt"
        val text = "It can watch and battle for this phone. Wasn't you? Open Hunt and tap Log out browsers."
        Log.print("  (website: ${title.lowercase()})")
        val n = NotificationCompat.Builder(this, "website")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
        runCatching { getSystemService(NotificationManager::class.java).notify(2, n) }
    }

    /** The button shows while Roblox is on screen, or a hunt is on. */
    private fun updateButton() = main { if (robloxShown || hunter != null) showButton() else hideButton() }

    @Volatile private var starting = false

    /** ON / OFF: starts a hunt, or pauses and resumes the one going. Anything
     *  that needs you (setup, allowing screen capture, a config mistake)
     *  opens the app. */
    private fun onButton() {
        hunter?.let {
            Log.print("  (ON / OFF tapped)")
            it.toggle()
            Hunt.refreshTile(this)
            return
        }
        if (starting) return
        starting = true
        Thread {
            val problem = Hunt.start(this)
            starting = false
            if (problem != null) startActivity(Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK).putExtra("start", problem))
        }.start()
    }

    /** A small square ON / OFF button floating over Roblox; drag it out of the
     *  way. It also hears every touch outside itself, which is how the hunter
     *  notices you're back. */
    @SuppressLint("ClickableViewAccessibility")
    private fun showButton() {
        if (button != null) return
        val wm = getSystemService(WindowManager::class.java)
        val dp = resources.displayMetrics.density
        val size = (40 * dp).roundToInt()  // for telling a tap from a drag
        val spot = Hunt.prefs(this)  // under the Mute button, where the last battle had it
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT, WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT).apply {
            // screen coordinates, the same as the screenshots'
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            // at first the margin right of the game; the left margin is where
            // chat lines are looked for
            gravity = Gravity.TOP or Gravity.END
            x = spot.getInt("button_x", (20 * dp).roundToInt())
            y = spot.getInt("button_y", resources.displayMetrics.heightPixels / 5)
        }
        buttonParams = params
        val b = TextView(this).apply {
            gravity = Gravity.CENTER
            textSize = 12f
            letterSpacing = 0.08f
            minWidth = (48 * dp).roundToInt()  // still easy to hit
            minHeight = (32 * dp).roundToInt()
            setPadding((12 * dp).roundToInt(), (6 * dp).roundToInt(), (12 * dp).roundToInt(), (6 * dp).roundToInt())
            background = GradientDrawable().apply {
                setColor(0x8C000000.toInt())  // see-through black: readable over any scene
                cornerRadius = 4 * dp
            }
        }
        var downX = 0f; var downY = 0f; var startX = 0; var startY = 0; var moved = false
        var ours = false  // the hunter's own tap or swipe landed on it: not yours
        b.setOnTouchListener { _, e ->
            when (e.actionMasked) {
                MotionEvent.ACTION_OUTSIDE -> {
                    // the hunter's own taps land outside too
                    if (!tapping && now() - tappedAt > 0.5) {
                        // Android says the screen was touched, not where
                        if (hunter != null && now() - touched > 3) Log.print("  (you touched the screen)")
                        touched = now()
                    }
                }
                MotionEvent.ACTION_DOWN -> {
                    downX = e.rawX; downY = e.rawY; startX = params.x; startY = params.y; moved = false
                    ours = tapping || now() - tappedAt < 0.3
                }
                MotionEvent.ACTION_MOVE -> if (!ours) {
                    if (abs(e.rawX - downX) + abs(e.rawY - downY) > size / 4) moved = true
                    params.x = startX - (e.rawX - downX).toInt()  // x counts from the right edge
                    params.y = startY + (e.rawY - downY).toInt()
                    wm.updateViewLayout(b, params)
                }
                MotionEvent.ACTION_UP -> if (ours) Unit else if (!moved) onButton() else dragged = true
            }
            true
        }
        wm.addView(b, params)
        button = b
        refreshButton()
    }

    private var buttonParams: WindowManager.LayoutParams? = null
    @Volatile private var placedUnder: Rect? = null
    @Volatile private var dragged = false  // moved by hand: it stays there

    override fun label() = "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}, " +
        "Android ${android.os.Build.VERSION.RELEASE}"

    override fun placeButton(mute: Rect) {
        if (dragged || mute == placedUnder) return
        placedUnder = mute
        val dp = resources.displayMetrics.density
        val k = shrink
        val screen = getSystemService(WindowManager::class.java).maximumWindowMetrics.bounds
        // its right edge a little in from Mute's, just below it
        val x = (screen.width() - mute.r * k + 8 * dp).roundToInt()
        val y = (mute.b * k + 8 * dp).roundToInt()
        Hunt.prefs(this).edit().putInt("button_x", x).putInt("button_y", y).apply()
        main {
            val p = buttonParams
            val b = button
            if (p != null && b != null && (p.x != x || p.y != y)) {
                p.x = x
                p.y = y
                getSystemService(WindowManager::class.java).updateViewLayout(b, p)
            }
        }
    }

    private fun refreshButton() {
        val b = button ?: return
        val on = hunter?.running() == true
        b.text = if (starting) "…" else if (on) "● ON" else "OFF"
        b.setTextColor(if (on) Color.WHITE else 0xFFA0A0A0.toInt())
        b.postDelayed({ refreshButton() }, 300)
    }

    private fun hideButton() {
        button?.let { getSystemService(WindowManager::class.java).removeView(it) }
        button = null
        buttonParams = null
    }

    // ------------------------------------------------------------ Phone

    /** Screenshots come upright and at most this wide: a 4K screen is
     *  halved, which keeps reading fast and is still sharper than needed. */
    private val MAX_WIDTH = 2000
    @Volatile private var shrink = 1.0  // screen px per screenshot px
    private var failures = 0

    override fun grab(): Shot {
        while (true) {
            val cap = CaptureService.instance
            if (cap != null) {  // screen capture instead: see screenshotsWork()
                cap.grab()?.let {
                    shrink = cap.shrink
                    return it
                }
                Thread.sleep(100)
                continue
            }
            shot(Int.MAX_VALUE)?.let { return it }
        }
    }

    /** Whether this Android's accessibility screenshots show anything: some
     *  emulators hand over black ones. Then MainActivity sets up screen
     *  capture (CaptureService) instead. */
    fun screenshotsWork(): Boolean {
        val lit = shot(5)?.let { com.pokehunt.core.brightness(it) } ?: 0u
        Log.print("  (screenshots: ${lit}% bright)")
        return lit > 0u
    }

    private fun shot(tries: Int): Shot? {
        repeat(tries) {
            // Android allows about three screenshots a second
            val wait = lastShot + 340 - System.currentTimeMillis()
            if (wait > 0) Thread.sleep(wait)
            lastShot = System.currentTimeMillis()
            var bitmap: Bitmap? = null
            var error = -1
            val done = CountDownLatch(1)
            takeScreenshot(Display.DEFAULT_DISPLAY, shots, object : TakeScreenshotCallback {
                override fun onSuccess(shot: ScreenshotResult) {
                    val hw = shot.hardwareBuffer
                    bitmap = Bitmap.wrapHardwareBuffer(hw, shot.colorSpace)?.copy(Bitmap.Config.ARGB_8888, false)
                    hw.close()
                    done.countDown()
                }

                override fun onFailure(errorCode: Int) {
                    error = errorCode
                    done.countDown()
                }
            })
            done.await(5, TimeUnit.SECONDS)
            val raw = bitmap
            if (raw == null) {
                if (++failures % 10 == 0) Log.print("  (Android gives no screenshot: error $error, ${failures}x)")
                return@repeat
            }
            failures = 0
            val bm = upright(raw)
            return rgba(bm).also { bm.recycle() }
        }
        return null
    }

    /** Some Androids hand over the screenshot as the screen is built (a phone:
     *  portrait) while Roblox shows landscape: turn it the way the screen
     *  shows it. And shrink big screens. */
    private fun upright(raw: Bitmap): Bitmap {
        val screen = getSystemService(WindowManager::class.java).maximumWindowMetrics.bounds
        val turned = (raw.width > raw.height) != (screen.width() > screen.height())
        val rotation = getSystemService(android.hardware.display.DisplayManager::class.java)
            .getDisplay(Display.DEFAULT_DISPLAY).rotation
        val wide = if (turned) raw.height else raw.width
        val k = if (wide > MAX_WIDTH) MAX_WIDTH.toFloat() / wide else 1f
        if (!turned && k == 1f) {
            shrink = 1.0
            return raw
        }
        val m = android.graphics.Matrix()
        // the content is drawn rotated by the display's rotation; undo that
        if (turned) m.postRotate(-90f * (if (rotation == 0 || rotation == 2) 1 else rotation))
        m.postScale(k, k)
        val out = Bitmap.createBitmap(raw, 0, 0, raw.width, raw.height, m, true)
        raw.recycle()
        shrink = 1.0 / k
        return out
    }

    /** A Bitmap's pixels as the hunter takes them (ARGB_8888 lies in memory as RGBA). */
    private fun rgba(bm: Bitmap): Shot {
        val buf = ByteBuffer.allocate(bm.width * bm.height * 4)
        bm.copyPixelsToBuffer(buf)
        return Shot(bm.width, bm.height, buf.array())
    }

    override fun tap(x: Double, y: Double) {
        val path = Path().apply { moveTo((x * shrink).toFloat(), (y * shrink).toFloat()) }
        // held 80 ms: Roblox reads input once a frame and misses instant taps
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, 80)).build()
        val done = CountDownLatch(1)
        tapping = true
        dispatchGesture(gesture, object : GestureResultCallback() {
            override fun onCompleted(g: GestureDescription?) = done.countDown()
            override fun onCancelled(g: GestureDescription?) = done.countDown()
        }, null)
        done.await(2, TimeUnit.SECONDS)
        tappedAt = now()
        tapping = false
        Thread.sleep(100)
    }

    override fun swipe(x0: Double, y0: Double, x1: Double, y1: Double, seconds: Double) {
        val k = shrink
        val path = Path().apply { moveTo((x0 * k).toFloat(), (y0 * k).toFloat()); lineTo((x1 * k).toFloat(), (y1 * k).toFloat()) }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, (seconds * 1000).toLong())).build()
        val done = CountDownLatch(1)
        tapping = true
        dispatchGesture(gesture, object : GestureResultCallback() {
            override fun onCompleted(g: GestureDescription?) = done.countDown()
            override fun onCancelled(g: GestureDescription?) = done.countDown()
        }, null)
        done.await(5, TimeUnit.SECONDS)
        tappedAt = now()
        tapping = false
        Thread.sleep(150)
    }

    override fun robloxInFront(): Boolean {
        val pkg = rootInActiveWindow?.packageName ?: return true  // can't tell: don't fight over it
        return pkg == ROBLOX || pkg == packageName
    }

    override fun focusRoblox() {
        packageManager.getLaunchIntentForPackage(ROBLOX)?.let {
            it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            startActivity(it)
        }
        Thread.sleep(800)
    }

    override fun idle(): Double? = if (touched == 0.0) null else now() - touched

    override fun buttonBox(): Rect? {
        val b = button ?: return null
        if (!b.isShown) return null
        val at = IntArray(2)
        b.getLocationOnScreen(at)
        val k = shrink
        return Rect((at[0] / k).toInt(), (at[1] / k).toInt(), ((at[0] + b.width) / k).toInt(), ((at[1] + b.height) / k).toInt())
    }

    override fun ocr(shot: Shot): List<com.pokehunt.core.Text> = runCatching {
        val bm = Bitmap.createBitmap(shot.width, shot.height, Bitmap.Config.ARGB_8888)
        bm.copyPixelsFromBuffer(ByteBuffer.wrap(shot.rgba))
        val result = Tasks.await(recognizer.process(InputImage.fromBitmap(bm, 0)), 10, TimeUnit.SECONDS)
        val w = bm.width.toDouble()
        val h = bm.height.toDouble()
        val out = ArrayList<com.pokehunt.core.Text>()
        // A line can span a whole row ("Quick Ball      x12"); split it where
        // the words are far apart, the way the Mac's text recognition does.
        for (block in result.textBlocks) for (line in block.lines) {
            val group = ArrayList<com.google.mlkit.vision.text.Text.Element>()
            fun flush() {
                val boxes = group.mapNotNull { it.boundingBox }
                if (boxes.isNotEmpty()) out.add(com.pokehunt.core.Text(group.joinToString(" ") { it.text },
                    (boxes.minOf { it.left } + boxes.maxOf { it.right }) / 2.0 / w,
                    (boxes.minOf { it.top } + boxes.maxOf { it.bottom }) / 2.0 / h))
                group.clear()
            }
            for (el in line.elements) {
                val prev = group.lastOrNull()?.boundingBox
                val cur = el.boundingBox
                if (prev != null && cur != null && cur.left - prev.right > 1.2 * max(cur.height(), prev.height())) flush()
                group.add(el)
            }
            flush()
        }
        bm.recycle()
        out
    }.getOrDefault(emptyList())

    override fun savePicture(png: ByteArray, name: String): String = runCatching {
        val file = SimpleDateFormat("yyyyMMdd-HHmmss-", Locale.US).format(Date()) + name + ".png"
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, file)
            put(MediaStore.Images.Media.MIME_TYPE, "image/png")
            put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/Hunt")
        }
        val uri = contentResolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values) ?: return@runCatching "-"
        contentResolver.openOutputStream(uri)?.use { it.write(png) }
        "Pictures/Hunt/$file"
    }.getOrDefault("-")

    override fun say(line: String) = Log.print(line)

    override fun code(code: String?) = Hunt.refreshTile(this)

    override fun sound(kind: String) {
        runCatching { playSound(kind) }
    }

    private fun playSound(kind: String) {
        val vib = getSystemService(Vibrator::class.java)
        when (kind) {
            "found" -> {
                vib.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 400, 200, 400, 200, 400), -1))
                RingtoneManager.getRingtone(this, RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION))?.play()
                val n = NotificationCompat.Builder(this, "finds")
                    .setSmallIcon(android.R.drawable.star_big_on)
                    .setContentTitle("Hunt found one")
                    .setContentText("Holding the battle for you: touch the screen to take over.")
                    .setPriority(NotificationCompat.PRIORITY_HIGH)
                    .build()
                runCatching { getSystemService(NotificationManager::class.java).notify(1, n) }
            }
            else -> {
                vib.vibrate(VibrationEffect.createOneShot(60, VibrationEffect.DEFAULT_AMPLITUDE))
                val tone = ToneGenerator(AudioManager.STREAM_NOTIFICATION, 60)
                tone.startTone(if (kind == "paused") ToneGenerator.TONE_PROP_NACK else ToneGenerator.TONE_PROP_ACK, 150)
                android.os.Handler(mainLooper).postDelayed({ tone.release() }, 500)
            }
        }
    }

    fun forgetCalibration() = com.pokehunt.core.forgetCalibration(filesDir.path)
}
