package com.pokehunt

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.drawable.GradientDrawable
import android.os.SystemClock
import android.text.InputType
import android.util.TypedValue
import android.view.GestureDetector
import android.view.Gravity
import android.view.MotionEvent
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.EditText
import android.widget.FrameLayout
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sin

/** The desktop's pill on a phone, floating over Roblox (HuntService), right after
 *  Roblox's own menu and chat buttons: play / stop at its left end (a long press is a
 *  debug run, and play turns into a bug while it's on) and the website's code; dragged
 *  by the rest of it. The webhook is set in the app.
 *  An update makes play's white circle stretch along it as it comes down. Drawn as
 *  window/src/main.rs draws it, in its look. */
@SuppressLint("ViewConstructor")
class PillView(ctx: Context, private val tall: Float = 40f) : FrameLayout(ctx) {
    /** What it does: the service's. */
    interface Actions {
        fun play()
        fun debug()
        fun code()
        fun newCode()
        fun drag(dx: Float, dy: Float, done: Boolean)
        fun outside()  // a touch somewhere else on the screen
        fun ours(): Boolean  // the hunter's own tap is landing: not yours
    }
    var actions: Actions? = null
    enum class State { SETUP, OFF, STARTING, HUNTING }

    var state = State.OFF
    var testing = false
    var code = ""      // the website's code, "" while it's on its way (three dots)
    var words = ""     // in the code's place when there's no code: "Off", "Turn on", "Code used"
    var progress: Float? = null  // an update: 0 to 1, 1 installing

    // the desktop pill's look (look(false))
    private val FILL = Color.rgb(0x11, 0x11, 0x11)
    private val INK = Color.WHITE
    private val DIM = Color.rgb(0x84, 0x84, 0x84)
    private val EDGE = Color.rgb(0x2c, 0x2c, 0x2c)
    private val BOXED = Color.rgb(0x1d, 0x1d, 0x1d)

    private fun dp(v: Float) = v * resources.displayMetrics.density
    private val h get() = dp(tall)
    private val inset get() = dp(tall / 7)
    private val control get() = h - 2 * inset
    private val born = SystemClock.uptimeMillis()

    private val inter = resources.getFont(R.font.inter_medium)
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = inter
        textSize = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, tall * 0.33f, resources.displayMetrics)
        letterSpacing = 0.06f
    }
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val bug = context.getDrawable(R.drawable.bug)!!.mutate()

    // what's pressed, for the give under the finger
    private var down: String? = null

    init {
        setWillNotDraw(false)
    }

    // ------------------------------------------------------------ easing, as egui's

    private val values = HashMap<String, Pair<Float, Long>>()

    /** Eases towards goal over seconds; keeps the drawing going till it's there. */
    private fun anim(key: String, goal: Float, seconds: Float): Float {
        val now = SystemClock.uptimeMillis()
        val (v, at) = values[key] ?: (goal to now)
        val step = (now - at) / 1000f / seconds
        val next = if (v < goal) min(goal, v + step) else max(goal, v - step)
        values[key] = next to now
        if (next != goal) postInvalidateOnAnimation()
        return next
    }

    private fun ease(x: Float) = 1 - (1 - x.coerceIn(0f, 1f)).pow(3)
    private fun spring(x: Float): Float {
        val t = x.coerceIn(0f, 1f) - 1
        val c = 1.2f
        return 1 + (c + 1) * t * t * t + c * t * t
    }

    // ------------------------------------------------------------ where things are

    private val playAt get() = inset + control / 2
    private val codeLeft get() = inset + control + dp(14f)
    private val codeRight get() = codeLeft + text.measureText("WWWWWW")

    override fun onMeasure(widthSpec: Int, heightSpec: Int) {
        // play and the code, the round end as far past it as play is from the left
        setMeasuredDimension((codeRight + h / 2).toInt(), h.toInt())
    }

    override fun onLayout(changed: Boolean, l: Int, t: Int, r: Int, b: Int) {}

    // ------------------------------------------------------------ the drawing

    override fun onDraw(canvas: Canvas) {
        val t = (SystemClock.uptimeMillis() - born) / 1000f
        // the opening: it stretches out from play's circle, what's in it fades in
        val grow = min(1f, spring(t / 0.55f))
        val reveal = ease((t - 0.3f) / 0.45f)
        if (reveal < 1f) postInvalidateOnAnimation()
        val shape = RectF(0f, 0f, h + (width - h) * grow, h)
        val r = h / 2
        paint.color = EDGE
        canvas.drawRoundRect(shape, r, r, paint)  // a hairline round it, for dark games
        paint.color = FILL
        val inner = RectF(shape.left + 1, shape.top + 1, shape.right - 1, shape.bottom - 1)
        canvas.drawRoundRect(inner, r - 1, r - 1, paint)

        // play / stop, melting into each other; the bug while a debug run's on
        val give = anim("press", if (down == "play") 0.9f else 1f, 0.12f)
        val cy = h / 2
        paint.color = if (state == State.SETUP) DIM else INK
        canvas.drawCircle(playAt, cy, control / 2 * give, paint)
        val stop = ease(anim("stop", if (state == State.HUNTING || state == State.STARTING) 1f else 0f, 0.28f))
        val bugness = ease(anim("bug", if (testing) 1f else 0f, 0.3f))
        val s = control / 2 * give
        val tri = arrayOf(-0.26f to -0.33f, 0.40f to -0.004f, 0.40f to 0.004f, -0.26f to 0.33f)
        val sq = arrayOf(-0.26f to -0.26f, 0.26f to -0.26f, 0.26f to 0.26f, -0.26f to 0.26f)
        val path = Path()
        for (i in 0..3) {
            val x = playAt + s * (tri[i].first + (sq[i].first - tri[i].first) * stop)
            val y = cy + s * (tri[i].second + (sq[i].second - tri[i].second) * stop)
            if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        path.close()
        paint.color = FILL
        paint.alpha = (255 * (1 - bugness)).toInt()
        canvas.drawPath(path, paint)
        paint.alpha = 255
        if (bugness > 0f) {
            val b = (s * 1.05f).toInt()
            bug.setBounds((playAt - b / 2).toInt(), (cy - b / 2).toInt(), (playAt + b / 2).toInt(), (cy + b / 2).toInt())
            bug.alpha = (255 * bugness).toInt()
            bug.draw(canvas)
        }

        // the website's code, or a word; three breathing dots while the code's on its way
        val baseline = cy - (text.ascent() + text.descent()) / 2
        val slide = (1 - reveal) * dp(10f)
        if (code.isNotEmpty()) {
            text.color = INK
            text.alpha = (255 * reveal).toInt()
            canvas.drawText(code, codeLeft - slide, baseline, text)
        } else if (state == State.HUNTING || state == State.STARTING) {
            for (i in 0..2) {
                val a = 0.5f + 0.5f * sin(t * 4f - i * 0.8f)
                paint.color = DIM
                paint.alpha = (255 * (0.35f + 0.65f * a) * reveal).toInt()
                canvas.drawCircle(codeLeft + dp(3f) + i * dp(9f) - slide, cy, dp(2.4f), paint)
            }
            paint.alpha = 255
            postInvalidateOnAnimation()
        } else if (words.isNotEmpty()) {
            text.color = DIM
            text.alpha = (255 * reveal).toInt()
            canvas.drawText(words, codeLeft - slide, baseline, text)
        }
        text.alpha = 255
    }

    override fun dispatchDraw(canvas: Canvas) {
        super.dispatchDraw(canvas)
        // an update on its way: the pill goes darker as what's in it fades, and play's
        // circle stretches rightwards along it as it comes down, "Installing" once across
        val updating = anim("updating", if (progress != null) 1f else 0f, 0.7f)
        if (updating <= 0f) return
        val dark = ease(updating / 0.55f)
        val words = ease((updating - 0.4f) / 0.6f)
        val r = h / 2
        val inner = RectF(1f, 1f, width - 1f, h - 1)
        paint.color = Color.BLACK
        paint.alpha = (255 * dark).toInt()
        canvas.drawRoundRect(inner, r - 1, r - 1, paint)
        val done = anim("progress", progress ?: 1f, 0.3f)
        val track = RectF(inset, inset, width - inset, h - inset)
        val bar = RectF(track.left, track.top, track.left + control + (track.width() - control) * done, track.bottom)
        paint.color = BOXED
        paint.alpha = (127 * words).toInt()
        canvas.drawRoundRect(track, control / 2, control / 2, paint)
        paint.color = INK
        paint.alpha = (255 * dark).toInt()
        canvas.drawRoundRect(bar, control / 2, control / 2, paint)
        val label = if (done < 0.995f) "Updating  ${(done * 100).toInt()}%" else "Installing"
        val x = track.centerX() - text.measureText(label) / 2
        val y = h / 2 - (text.ascent() + text.descent()) / 2
        // the words white on the black, black on the white
        canvas.save()
        canvas.clipRect(bar.right, 0f, width.toFloat(), h)
        text.color = INK
        text.alpha = (255 * words).toInt()
        canvas.drawText(label, x, y, text)
        canvas.restore()
        canvas.save()
        canvas.clipRect(bar)
        text.color = Color.BLACK
        text.alpha = (255 * words).toInt()
        canvas.drawText(label, x, y, text)
        canvas.restore()
        text.alpha = 255
        paint.alpha = 255
        postInvalidateOnAnimation()
    }

    // ------------------------------------------------------------ taps, drags

    private val gestures = GestureDetector(ctx, object : GestureDetector.SimpleOnGestureListener() {
        override fun onDown(e: MotionEvent) = true
        override fun onSingleTapUp(e: MotionEvent): Boolean {
            when (hit(e)) {
                "play" -> actions?.play()
                "code" -> actions?.code()
            }
            return true
        }
        override fun onLongPress(e: MotionEvent) {
            if (dragging) return
            when (hit(e)) {
                "play" -> actions?.debug()
                "code" -> actions?.newCode()
            }
        }
    })

    private fun hit(e: MotionEvent): String? = when {
        progress != null -> null  // nothing under the bar to tap
        e.x < inset + control + dp(6f) -> "play"
        e.x < codeRight + dp(6f) -> "code"
        else -> null
    }

    private var downX = 0f
    private var downY = 0f
    private var dragging = false
    private var ignore = false

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(e: MotionEvent): Boolean {
        when (e.actionMasked) {
            MotionEvent.ACTION_OUTSIDE -> {
                actions?.outside()
                return true
            }
            MotionEvent.ACTION_DOWN -> {
                ignore = actions?.ours() == true
                downX = e.rawX
                downY = e.rawY
                dragging = false
                down = hit(e)
            }
            MotionEvent.ACTION_MOVE -> if (!ignore) {
                val (dx, dy) = e.rawX - downX to e.rawY - downY
                if (!dragging && kotlin.math.abs(dx) + kotlin.math.abs(dy) > dp(10f)) {
                    dragging = true
                    down = null
                }
                if (dragging) actions?.drag(dx, dy, false)
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                if (dragging) actions?.drag(e.rawX - downX, e.rawY - downY, true)
                down = null
            }
        }
        invalidate()
        if (ignore || dragging) return true
        return gestures.onTouchEvent(e)
    }
}
