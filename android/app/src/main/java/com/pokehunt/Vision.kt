package com.pokehunt

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

// Plain Kotlin (no Android classes), so it's tested against samples/*.png on
// the computer. Ported from pokehunt.py.

/** A screenshot: ARGB pixels, row by row. */
class Frame(val width: Int, val height: Int, val px: IntArray) {
    fun r(i: Int) = (px[i] shr 16) and 255
    fun g(i: Int) = (px[i] shr 8) and 255
    fun b(i: Int) = px[i] and 255

    fun clip(area: Box) = Box(area.l.coerceIn(0, width), area.t.coerceIn(0, height),
                              area.r.coerceIn(0, width), area.b.coerceIn(0, height))

    /** The pixel indexes inside area. */
    inline fun each(area: Box, f: (Int) -> Unit) {
        val c = clip(area)
        for (y in c.t until c.b) for (x in c.l until c.r) f(y * width + x)
    }

    /** Fraction of the pixels inside area that pass test. */
    inline fun share(area: Box, test: (Int) -> Boolean): Double {
        var n = 0
        var hit = 0
        each(area) { i -> n++; if (test(i)) hit++ }
        return if (n == 0) 0.0 else hit.toDouble() / n
    }

    fun mask(area: Box, test: (Int) -> Boolean): Mask {
        val c = clip(area)
        val m = Mask(c, BooleanArray(c.w * c.h))
        for (y in c.t until c.b) for (x in c.l until c.r)
            m.on[(y - c.t) * c.w + (x - c.l)] = test(y * width + x)
        return m
    }

    /** Median color of the pixels inside area that pass test (all of them by default). */
    fun median(area: Box, test: (Int) -> Boolean = { true }): IntArray? {
        val hist = Array(3) { IntArray(256) }
        var n = 0
        each(area) { i ->
            if (test(i)) { hist[0][r(i)]++; hist[1][g(i)]++; hist[2][b(i)]++; n++ }
        }
        if (n == 0) return null
        return IntArray(3) { ch ->
            var seen = 0
            var v = 0
            while (seen + hist[ch][v] <= n / 2 && v < 255) seen += hist[ch][v++]
            v
        }
    }

    fun near(i: Int, c: IntArray, tol: Int) =
        abs(r(i) - c[0]) < tol && abs(g(i) - c[1]) < tol && abs(b(i) - c[2]) < tol
}

data class Box(val l: Int, val t: Int, val r: Int, val b: Int) {
    val w get() = r - l
    val h get() = b - t
    fun overlaps(o: Box) = l < o.r && o.l < r && t < o.b && o.t < b
}

data class Pt(val x: Double, val y: Double)

/** Pixels that are on, inside a box of the frame. */
class Mask(val box: Box, val on: BooleanArray) {
    /** 4-connected patches of on pixels, as (screen box, pixel count). */
    fun patches(): List<Pair<Box, Int>> {
        val w = box.w
        val h = box.h
        val seen = BooleanArray(on.size)
        val stack = IntArray(on.size)
        val out = ArrayList<Pair<Box, Int>>()
        for (start in on.indices) {
            if (!on[start] || seen[start]) continue
            var top = 0
            stack[top++] = start
            seen[start] = true
            var n = 0
            var x0 = w; var y0 = h; var x1 = 0; var y1 = 0
            while (top > 0) {
                val i = stack[--top]
                n++
                val x = i % w
                val y = i / w
                x0 = min(x0, x); x1 = max(x1, x); y0 = min(y0, y); y1 = max(y1, y)
                if (x > 0 && on[i - 1] && !seen[i - 1]) { seen[i - 1] = true; stack[top++] = i - 1 }
                if (x < w - 1 && on[i + 1] && !seen[i + 1]) { seen[i + 1] = true; stack[top++] = i + 1 }
                if (y > 0 && on[i - w] && !seen[i - w]) { seen[i - w] = true; stack[top++] = i - w }
                if (y < h - 1 && on[i + w] && !seen[i + w]) { seen[i + w] = true; stack[top++] = i + w }
            }
            out.add(Box(box.l + x0, box.t + y0, box.l + x1 + 1, box.t + y1 + 1) to n)
        }
        return out
    }
}

// ---------------------------------------------------------------- layout
// Measured on samples/*.png (a 2000 px wide Roblox window on a Mac).
// Everything is placed relative to the Mute button; how a phone stretches the
// game differently across and down is calibrated (Calibration below).

object Ref {
    val MUTE = Box(1533, 153, 1749, 218)
    val BOX = Box(370, 317, 890, 535)          // enemy box plus the gradient tab under it
    const val HP_BAR_END = 435                 // below this, inside the box: the reskin
    const val BOX_END = 487                    // below this: the gradient tab
    val ICONS = Box(395, 425, 525, 490)        // shiny star / tint sparkle
    val PANEL = Box(300, 140, 1750, 1220)      // the whole game, for screenshots
    val FOE_SPRITE = Pt(1170.0, 285.0)         // top left of the wild Pokemon's 96 x 96 sprite (Gen 5),
    const val SPRITE_PIXEL = 4.165             // drawn this many reference pixels per sprite pixel
    val BUTTONS = linkedMapOf(                 // area, usual color
        "fight" to (Box(305, 1025, 655, 1115) to intArrayOf(210, 219, 189)),
        "switch" to (Box(668, 1025, 1020, 1115) to intArrayOf(145, 170, 157)),
        "bag" to (Box(1030, 1025, 1383, 1115) to intArrayOf(61, 92, 111)),
        "run" to (Box(1393, 1025, 1745, 1115) to intArrayOf(26, 51, 66)))
    val RUN = Pt(1568.0, 1070.0)
    val BAG = Pt(1206.0, 1070.0)
    val FIGHT = Pt(480.0, 1070.0)
    val MSG = Box(300, 885, 1750, 1020)          // "What should Clodsire do?"
    val PLAYER_BOX = Box(1168, 685, 1680, 870)   // your Pokemon's box, HP "350 / 376"
    const val BAG_SPLIT = 1100.0   // bag rows' names are left of this, the search box right
    const val BAG_TAG_L = 1320.0   // across: inside a bag row's count tag, left of its "x985"
    const val BAG_TAG_R = 1380.0
    val AFK_SPOT = Pt(150.0, 800.0)              // margin left of the game
    val CHAT_MARGIN = Box(80, 140, 270, 560)     // margin where chat lines start
    val IDLE_SPOT = Pt(1550.0, 960.0)            // empty right end of the "What should ... do?" box

    // Found on screen to calibrate, where the samples have them
    val ANCHORS = linkedMapOf(
        "fight" to Box(300, 1020, 661, 1120), "switch" to Box(663, 1020, 1024, 1119),
        "bag" to Box(1025, 1020, 1388, 1119), "run" to Box(1389, 1020, 1750, 1120),
        "box" to Box(373, 319, 880, 485))
    val BOX_GRAY = intArrayOf(143, 143, 143)
}

/** kx, ky: how much more (or less) the game is stretched across / down than
 *  the Mute button's width says; dx, dy: shifted by (reference px). Plus the
 *  colors seen on this screen. */
data class Calibration(val kx: Double = 1.0, val ky: Double = 1.0,
                       val dx: Double = 0.0, val dy: Double = 0.0,
                       val colors: Map<String, IntArray> = emptyMap(),
                       val miss: Double = 0.0)

class Layout(val mute: Box, val cal: Calibration) {
    val s = mute.w.toDouble() / Ref.MUTE.w

    fun pt(x: Double, y: Double) = Pt(
        mute.l + ((x - Ref.MUTE.l) * cal.kx + cal.dx) * s,
        mute.t + ((y - Ref.MUTE.t) * cal.ky + cal.dy) * s)

    fun pt(p: Pt) = pt(p.x, p.y)

    fun box(b: Box): Box {
        val a = pt(b.l.toDouble(), b.t.toDouble())
        val z = pt(b.r.toDouble(), b.b.toDouble())
        return Box(max(a.x.toInt(), 0), max(a.y.toInt(), 0), z.x.toInt(), z.y.toInt())
    }
}

// ---------------------------------------------------------------- reading the screen

object Vision {
    fun red(f: Frame, i: Int) = f.r(i) > 150 && f.g(i) < 50 && f.b(i) < 50

    /** The wild Pokemon's 96 x 96 sprite area as the game drew it (shiny,
     *  gradient, reskin and all), for the website, which cuts it out with the
     *  species' shape. The sprite keeps its shape: it's scaled like the
     *  screen's height (phones squeeze the battle). */
    fun spriteArea(f: Frame, lay: Layout): Frame {
        val o = lay.pt(Ref.FOE_SPRITE)
        val k = Ref.SPRITE_PIXEL * lay.s * lay.cal.ky
        val px = IntArray(96 * 96)
        for (j in 0 until 96) {
            val y = (o.y + (j + 0.5) * k).toInt().coerceIn(0, f.height - 1)
            for (i in 0 until 96) {
                val x = (o.x + (i + 0.5) * k).toInt().coerceIn(0, f.width - 1)
                px[j * 96 + i] = f.px[y * f.width + x] or 0xFF000000.toInt()
            }
        }
        return Frame(96, 96, px)
    }

    /** Red button with white text, 3.3x as wide as tall. */
    fun isMute(f: Frame, b: Box): Boolean {
        if (b.h < 12 || b.w.toDouble() / b.h !in 2.6..4.2) return false
        if (f.clip(b).w == 0) return false
        val white = f.share(b) { i -> min(f.r(i), min(f.g(i), f.b(i))) > 200 }
        return f.share(b) { i -> red(f, i) } > 0.6 && white > 0.05 && white < 0.4
    }

    /** The Mute button, or null. Checks the last known spot first, which is
     *  much cheaper than searching the whole screen. */
    fun findMute(f: Frame, hint: Box? = null): Box? {
        if (hint != null && isMute(f, hint)) return hint
        val all = Box(0, 0, f.width, f.height)
        return f.mask(all) { i -> red(f, i) }.patches().map { it.first }.firstOrNull { isMute(f, it) }
    }

    /** Fight / Switch / Bag / Run are showing (one may be lit up by a tap). */
    fun menuReady(f: Frame, lay: Layout): Boolean = Ref.BUTTONS.count { (name, v) ->
        val (area, color) = v
        val m = f.median(lay.box(area))
        m != null && listOfNotNull(color, lay.cal.colors[name]).any { c ->
            (0..2).all { abs(m[it] - c[it]) < 30 }
        }
    } >= 3

    /** The enemy's gray box isn't covered (by the chat, say): every quarter of
     *  it is mostly its gray. */
    fun boxVisible(f: Frame, lay: Layout): Boolean {
        val b = lay.box(Box(Ref.BOX.l, Ref.BOX.t, 882, Ref.BOX_END))
        val gray = lay.cal.colors["box"] ?: Ref.BOX_GRAY
        val q = b.w / 4
        return (0 until 4).all { k ->
            f.share(Box(b.l + k * q, b.t, b.l + (k + 1) * q, b.b)) { i -> f.near(i, gray, 18) } > 0.5
        }
    }

    /** Chat messages are on screen. They float over the enemy's box and get
     *  read as its name, so nothing is read while they show. */
    fun chatShowing(f: Frame, lay: Layout): Boolean {
        var b = f.clip(lay.box(Ref.CHAT_MARGIN))
        if (b.w <= 0 || b.h <= 0) return false
        // On a wide screen (a phone) Roblox's own menu and chat buttons reach
        // into the top of this strip, dark gray pills: start below them.
        val pill = (0 until b.h / 3).lastOrNull { row ->
            f.share(Box(b.l, b.t + row, b.r, b.t + row + 1)) { i ->
                val hi = max(f.r(i), max(f.g(i), f.b(i)))
                val lo = min(f.r(i), min(f.g(i), f.b(i)))
                hi - lo < 12 && hi in 15..60
            } > 0.15
        }
        if (pill != null) b = Box(b.l, b.t + pill + 3, b.r, b.b)
        return b.h > 0 && f.share(b) { i -> max(f.r(i), max(f.g(i), f.b(i))) > 90 } > 0.003
    }

    /** The bag's row at y is a favorite: its count tag ("x985") is yellow;
     *  the others' are light blue. As pokehunt.py's favorited. */
    fun favorited(f: Frame, texts: List<TextHit>, y: Double, lay: Layout): Boolean {
        val v = lay.s * lay.cal.ky
        val spots = mutableListOf(lay.pt(Ref.BAG_TAG_L, 0.0).x to lay.pt(Ref.BAG_TAG_R, 0.0).x)
        texts.firstOrNull { COUNT.matches(it.text) && abs(it.y - y) < 30 * v }?.let { spots.add(it.x - 110 * v to it.x - 70 * v) }
        return spots.any { (x0, x1) ->
            val area = Box(x0.toInt().coerceAtLeast(0), (y - 10 * v).toInt().coerceAtLeast(0), x1.toInt().coerceAtLeast(0), (y + 10 * v).toInt())
            area.w > 0 && f.share(area) { i -> f.r(i) > 200 && f.g(i) > 150 && f.b(i) < 150 } > 0.4
        }
    }

    /** A row that isn't a favorite is in sight, and no search is narrowing
     *  the list: favorites sit at the top, so then all of them are in sight. */
    fun plainRowInSight(f: Frame, texts: List<TextHit>, split: Double, lay: Layout): Boolean =
        texts.any { Rules.norm(it.text).startsWith("search") && it.x > split } &&
            texts.any { COUNT.matches(it.text) && it.x > split && !favorited(f, texts, it.y, lay) }

    private val COUNT = Regex("x[\\d,.]+", RegexOption.IGNORE_CASE)

    /** Roblox's chat button: the right end of the dark pill (menu, chat) in
     *  the top-left corner. Roblox opens its chat window when messages come in,
     *  and on a phone it covers the game; this button closes it. */
    fun chatButton(f: Frame): Pt? {
        val area = Box(0, 0, (f.width * 0.3).toInt(), (f.height * 0.15).toInt())
        val pill = f.mask(area) { i ->
            val hi = max(f.r(i), max(f.g(i), f.b(i)))
            val lo = min(f.r(i), min(f.g(i), f.b(i)))
            hi - lo < 12 && hi in 15..60
        }.patches().map { it.first }.filter {
            it.h > f.height * 0.03 && it.h < f.height * 0.12 && it.w.toDouble() / it.h in 1.5..4.0 &&
                it.l < f.width * 0.18 && it.r < f.width * 0.29
        }.minByOrNull { it.t } ?: return null  // the topmost: Roblox's own bar, not the chat window
        return Pt(pill.r - pill.h * 0.6, (pill.t + pill.b) / 2.0)
    }

    /** (shiny, tint): dark blue star / yellow sparkle in the enemy's box. */
    fun icons(f: Frame, lay: Layout): Pair<Boolean, Boolean> {
        val b = lay.box(Ref.ICONS)
        val shiny = f.share(b) { i -> f.b(i) > 100 && f.r(i) < 60 && f.g(i) < 60 } > 0.008
        val tint = f.share(b) { i -> f.r(i) > 220 && f.g(i) > 200 && f.b(i) < 160 } > 0.008
        return shiny to tint
    }

    /** How much of the screen isn't black, in %: 0 means the screenshot is blank. */
    fun brightness(f: Frame): Int {
        var lit = 0
        for (i in f.px.indices step 97) if (f.r(i) + f.g(i) + f.b(i) > 30) lit++
        return lit * 100 / (f.px.size / 97 + 1)
    }

    /** Center of the bright green Use button under the backpack row. */
    fun findUseButton(f: Frame, below: Double): Pt? =
        f.mask(Box(0, 0, f.width, f.height)) { i -> f.g(i) > 200 && f.r(i) < 170 && f.b(i) < 170 }
            .patches().map { it.first }
            .filter { it.h > 8 && it.w.toDouble() / it.h in 3.0..12.0 && it.t > below }
            .maxByOrNull { it.w * it.h }
            ?.let { Pt((it.l + it.r) / 2.0, (it.t + it.b) / 2.0) }
}

// ---------------------------------------------------------------- calibration

object Calibrate {
    /** Screen box of the patch of color around where lay puts ref, if it's
     *  about the expected size. The search reaches far: on a phone the game
     *  may be stretched a lot more down than across. */
    fun findBlock(f: Frame, lay: Layout, ref: Box, color: IntArray): Box? {
        val cx = (ref.l + ref.r) / 2.0
        val cy = (ref.t + ref.b) / 2.0
        val padX = ref.w / 2.0 + 0.3 * abs(cx - Ref.MUTE.l)
        val padY = ref.h / 2.0 + 0.3 * abs(cy - Ref.MUTE.t)
        val area = lay.box(Box((ref.l - padX).toInt(), (ref.t - padY).toInt(),
                               (ref.r + padX).toInt(), (ref.b + padY).toInt()))
        for (tol in intArrayOf(20, 35)) {
            val best = f.mask(area) { i -> f.near(i, color, tol) }.patches().maxByOrNull { it.second }
                ?: continue
            val b = best.first
            val w = b.w / (ref.w * lay.s)
            val h = b.h / (ref.h * lay.s)
            if (w in 0.6..1.6 && h in 0.6..1.6 && best.second > 0.5 * b.w * b.h) return b
        }
        return null
    }

    /** Fits the layout to the Fight/Switch/Bag/Run buttons and the enemy's box
     *  on this screen. Null if too little of the battle screen was found (the
     *  menu isn't up, say) or the fit doesn't read as a battle. */
    fun fit(f: Frame, mute: Box): Calibration? {
        val flat = Layout(mute, Calibration())
        val refs = ArrayList<Box>()
        val got = ArrayList<Box>()
        val colors = HashMap<String, IntArray>()
        for ((name, ref) in Ref.ANCHORS) {
            val color = if (name == "box") Ref.BOX_GRAY else Ref.BUTTONS.getValue(name).second
            val b = findBlock(f, flat, ref, color) ?: continue
            refs.add(ref); got.add(b)
            colors[name] = f.median(b) { i -> f.near(i, color, 35) } ?: color
        }
        if (refs.size - (if ("box" in colors) 1 else 0) < 2) return null
        refs.add(Ref.MUTE); got.add(mute)
        val s = flat.s
        val rx = refs.flatMap { listOf(it.l, it.r) }.map { (it - Ref.MUTE.l).toDouble() }
        val gx = got.flatMap { listOf(it.l, it.r) }.map { (it - mute.l) / s }
        val ry = refs.flatMap { listOf(it.t, it.b) }.map { (it - Ref.MUTE.t).toDouble() }
        val gy = got.flatMap { listOf(it.t, it.b) }.map { (it - mute.t) / s }
        val (kx, dx) = line(rx, gx)
        val (ky, dy) = line(ry, gy)
        if (kx !in 0.6..1.6 || ky !in 0.6..1.6) return null
        val miss = max(rx.indices.maxOf { abs(kx * rx[it] + dx - gx[it]) },
                       ry.indices.maxOf { abs(ky * ry[it] + dy - gy[it]) })
        if (miss > 12) return null  // reference px: the anchors don't agree on a layout
        val cal = Calibration(kx, ky, dx, dy, colors, miss * s)
        return if (Vision.menuReady(f, Layout(mute, cal))) cal else null
    }

    /** Least squares y = k x + d. */
    private fun line(x: List<Double>, y: List<Double>): Pair<Double, Double> {
        val mx = x.average()
        val my = y.average()
        val k = x.indices.sumOf { (x[it] - mx) * (y[it] - my) } / x.indices.sumOf { (x[it] - mx) * (x[it] - mx) }
        return k to my - k * mx
    }
}
