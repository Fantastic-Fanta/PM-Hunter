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
import android.os.Environment
import android.os.VibrationEffect
import android.os.Vibrator
import android.provider.MediaStore
import android.view.Display
import android.view.Gravity
import android.view.MotionEvent
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.widget.TextView
import androidx.core.app.NotificationCompat
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt

/** The hunter's hands and eyes on the phone: an accessibility service, which
 *  may take screenshots (Android 11+) and tap for you. It also shows the
 *  pause button floating over Roblox, and notices you touching the screen. */
class HuntService : AccessibilityService(), Phone {
    companion object {
        const val ROBLOX = "com.roblox.client"
        @Volatile var instance: HuntService? = null
            private set
    }

    @Volatile var hunter: Hunter? = null
        private set
    /** The hunt's link to the website; its code shows in the app. */
    @Volatile var remote: Remote? = null
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

    fun startHunt(cfg: Config, watch: Boolean, debug: Boolean = false) {
        endHunter()
        val h = Hunter(this, cfg, watch, debug)
        val build = packageManager.getPackageInfo(packageName, 0).versionName ?: ""
        // watching never taps, so there's nothing to take over
        val r = if (watch) null else Remote(Hunt.deviceKey(this), label(), build) { fromWebsite(h, it) }
        h.link = r
        hunter = h
        remote = r
        r?.start()
        thread = Thread({ h.run() }, "hunter").apply { start() }
        updateButton()
        Hunt.refreshTile(this)
    }

    fun stopHunt() {
        stopService(Intent(this, CaptureService::class.java))
        endHunter()
    }

    private fun endHunter() {
        remote?.stop()
        remote = null
        hunter?.stopped = true
        thread?.interrupt()
        hunter = null
        thread = null
        updateButton()
        Hunt.refreshTile(this)
    }

    private fun main(f: () -> Unit) = android.os.Handler(mainLooper).post(f)

    /** A command from the website (the poll thread). */
    private fun fromWebsite(h: Hunter, cmd: JSONObject) {
        when (cmd.optString("cmd")) {
            "takeover" -> h.takeOver()
            "resume" -> h.handBack()
            "shot" -> h.wantShot = true
            "login" -> websiteLogin()
            else -> h.commands.put(cmd)
        }
        Hunt.refreshTile(this)
    }

    /** Someone logged in with this phone's code. */
    private fun websiteLogin() {
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
    @Volatile private var placedUnder: Box? = null
    @Volatile private var dragged = false  // moved by hand: it stays there

    override fun label() = "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}, " +
        "Android ${android.os.Build.VERSION.RELEASE}"

    override fun placeButton(mute: Box) {
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
        val on = hunter?.running == true
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

    override fun grab(): Frame {
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
    fun screenshotsWork() = shot(5)?.let { Vision.brightness(it) > 0 } ?: false

    private fun shot(tries: Int): Frame? {
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
            val px = IntArray(bm.width * bm.height)
            bm.getPixels(px, 0, bm.width, 0, 0, bm.width, bm.height)
            bm.recycle()
            return Frame(bm.width, bm.height, px)
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

    override fun touchedAt() = touched

    override fun buttonBox(): Box? {
        val b = button ?: return null
        if (!b.isShown) return null
        val at = IntArray(2)
        b.getLocationOnScreen(at)
        val k = shrink
        return Box((at[0] / k).toInt(), (at[1] / k).toInt(), ((at[0] + b.width) / k).toInt(), ((at[1] + b.height) / k).toInt())
    }

    override fun ocr(f: Frame, area: Box, zoom: Double): List<TextHit> {
        val a = f.clip(area)
        if (a.w < 8 || a.h < 8) return emptyList()
        var bm = Bitmap.createBitmap(f.px, a.t * f.width + a.l, f.width, a.w, a.h, Bitmap.Config.ARGB_8888)
        if (zoom > 1.01) bm = Bitmap.createScaledBitmap(bm, (a.w * zoom).toInt(), (a.h * zoom).toInt(), true)
        val result = Tasks.await(recognizer.process(InputImage.fromBitmap(bm, 0)), 10, TimeUnit.SECONDS)
        val w = bm.width.toDouble()
        val h = bm.height.toDouble()
        val out = ArrayList<TextHit>()
        // A line can span a whole row ("Quick Ball      x12"); split it where
        // the words are far apart, the way the Mac's text recognition does.
        for (block in result.textBlocks) for (line in block.lines) {
            val group = ArrayList<com.google.mlkit.vision.text.Text.Element>()
            fun flush() {
                val boxes = group.mapNotNull { it.boundingBox }
                if (boxes.isNotEmpty()) out.add(TextHit(group.joinToString(" ") { it.text },
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
        return out
    }

    override fun png(f: Frame, area: Box?, maxWidth: Int): ByteArray {
        val a = f.clip(area ?: Box(0, 0, f.width, f.height))
        var bm = Bitmap.createBitmap(f.px, a.t * f.width + a.l, f.width, a.w, a.h, Bitmap.Config.ARGB_8888)
        if (bm.width > maxWidth) bm = Bitmap.createScaledBitmap(bm, maxWidth, bm.height * maxWidth / bm.width, true)
        return ByteArrayOutputStream().also { bm.compress(Bitmap.CompressFormat.PNG, 100, it) }.toByteArray()
    }

    override fun jpg(f: Frame, area: Box?, maxWidth: Int): ByteArray {
        val a = f.clip(area ?: Box(0, 0, f.width, f.height))
        var bm = Bitmap.createBitmap(f.px, a.t * f.width + a.l, f.width, a.w, a.h, Bitmap.Config.ARGB_8888)
        if (bm.width > maxWidth) bm = Bitmap.createScaledBitmap(bm, maxWidth, bm.height * maxWidth / bm.width, true)
        return ByteArrayOutputStream().also { bm.compress(Bitmap.CompressFormat.JPEG, 70, it) }.toByteArray()
    }

    override fun savePicture(png: ByteArray, name: String): String {
        val file = SimpleDateFormat("yyyyMMdd-HHmmss-", Locale.US).format(Date()) + name + ".png"
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, file)
            put(MediaStore.Images.Media.MIME_TYPE, "image/png")
            put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/Hunt")
        }
        val uri = contentResolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values) ?: return "-"
        contentResolver.openOutputStream(uri)?.use { it.write(png) }
        return "Pictures/Hunt/$file"
    }

    override fun markedShot(f: Frame, from: Pt, to: Pt?, maxWidth: Int): ByteArray {
        val k = minOf(1.0, maxWidth.toDouble() / f.width).toFloat()
        val src = Bitmap.createBitmap(f.px, f.width, f.height, Bitmap.Config.ARGB_8888)
        val bm = Bitmap.createScaledBitmap(src, (f.width * k).toInt(), (f.height * k).toInt(), true)
            .copy(Bitmap.Config.ARGB_8888, true)
        val c = Canvas(bm)
        val pen = Paint().apply { style = Paint.Style.STROKE; strokeWidth = 4f; color = Color.MAGENTA; isAntiAlias = true }
        val x = from.x.toFloat() * k
        val y = from.y.toFloat() * k
        c.drawCircle(x, y, 14f, pen)
        c.drawLine(x - 22, y, x + 22, y, pen)
        c.drawLine(x, y - 22, x, y + 22, pen)
        if (to != null) {
            c.drawLine(x, y, to.x.toFloat() * k, to.y.toFloat() * k, pen)
            c.drawCircle(to.x.toFloat() * k, to.y.toFloat() * k, 8f, pen)
        }
        return ByteArrayOutputStream().also { bm.compress(Bitmap.CompressFormat.JPEG, 70, it) }.toByteArray()
    }

    override fun drawLayout(f: Frame, lay: Layout): ByteArray {
        val bm = Bitmap.createBitmap(f.px, f.width, f.height, Bitmap.Config.ARGB_8888).copy(Bitmap.Config.ARGB_8888, true)
        val c = Canvas(bm)
        val line = Paint().apply { style = Paint.Style.STROKE; strokeWidth = 4f; color = Color.MAGENTA }
        val dot = Paint().apply { style = Paint.Style.STROKE; strokeWidth = 4f; color = Color.GREEN }
        val boxes = listOf(Ref.BOX, Ref.ICONS, Ref.MSG, Ref.PLAYER_BOX, Ref.CHAT_MARGIN, Ref.PANEL) +
            Ref.BUTTONS.values.map { it.first }
        for (b in boxes) lay.box(b).let { c.drawRect(it.l.toFloat(), it.t.toFloat(), it.r.toFloat(), it.b.toFloat(), line) }
        for (y in listOf(Ref.HP_BAR_END, Ref.BOX_END)) {
            val a = lay.pt(Ref.BOX.l.toDouble(), y.toDouble())
            val z = lay.pt(Ref.BOX.r.toDouble(), y.toDouble())
            c.drawLine(a.x.toFloat(), a.y.toFloat(), z.x.toFloat(), z.y.toFloat(), line)
        }
        for (p in listOf(Ref.RUN, Ref.BAG, Ref.FIGHT, Ref.IDLE_SPOT, Ref.AFK_SPOT))
            lay.pt(p).let { c.drawCircle(it.x.toFloat(), it.y.toFloat(), 12f, dot) }
        return ByteArrayOutputStream().also { bm.compress(Bitmap.CompressFormat.PNG, 100, it) }.toByteArray()
    }

    override fun logEncounter(row: List<String>) {
        val file = File(filesDir, "encounters.csv")
        if (!file.exists()) file.writeText("time,species,level,shiny,tint,reskin,gradient,action\n")
        val time = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())
        file.appendText((listOf(time) + row).joinToString(",") { csv(it) } + "\n")
    }

    private fun csv(s: String) = if (s.any { it == ',' || it == '"' || it == '\n' }) "\"${s.replace("\"", "\"\"")}\"" else s

    override fun sound(kind: String) {
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

    override fun loadCalibration(key: String): Calibration? {
        val s = getSharedPreferences("calibration", MODE_PRIVATE).getString(key, null) ?: return null
        return runCatching {
            val o = JSONObject(s)
            val colors = o.getJSONObject("colors").let { c ->
                c.keys().asSequence().associateWith { k -> c.getJSONArray(k).let { a -> IntArray(3) { a.getInt(it) } } }
            }
            Calibration(o.getDouble("kx"), o.getDouble("ky"), o.getDouble("dx"), o.getDouble("dy"), colors, o.getDouble("miss"))
        }.getOrNull()
    }

    override fun saveCalibration(key: String, cal: Calibration) {
        val o = JSONObject().put("kx", cal.kx).put("ky", cal.ky).put("dx", cal.dx).put("dy", cal.dy).put("miss", cal.miss)
            .put("colors", JSONObject(cal.colors.mapValues { org.json.JSONArray(it.value.toList()) }))
        getSharedPreferences("calibration", MODE_PRIVATE).edit().putString(key, o.toString()).apply()
    }

    fun forgetCalibration() = getSharedPreferences("calibration", MODE_PRIVATE).edit().clear().apply()
}
