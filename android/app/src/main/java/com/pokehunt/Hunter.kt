package com.pokehunt

import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.math.abs

/** What the hunter needs from the phone (HuntService does it on Android). */
interface Phone {
    fun grab(): Frame
    fun tap(x: Double, y: Double)
    /** Drags a finger from (x0, y0) to (x1, y1) over that many seconds. */
    fun swipe(x0: Double, y0: Double, x1: Double, y1: Double, seconds: Double)
    fun robloxInFront(): Boolean
    fun focusRoblox()
    /** Where the ON / OFF button is on the screenshots, to not read it as the game's. */
    fun buttonBox(): Box?
    /** When you last touched the screen yourself (seconds, like now()). */
    fun touchedAt(): Double
    /** Text read in area of f, as fractions of area, enlarged first by zoom. */
    fun ocr(f: Frame, area: Box, zoom: Double = 1.0): List<TextHit>
    fun png(f: Frame, area: Box?, maxWidth: Int): ByteArray
    /** Puts text into the text field that has the focus (Roblox opens a real one
     *  over a tapped TextBox, like the bag's search box). True if it took. */
    fun typeText(text: String): Boolean
    /** Keeps the on-screen keyboard from showing (true), or lets it show again. */
    fun keyboardHidden(hidden: Boolean)
    /** A JPEG of area of f, for the website. */
    fun jpg(f: Frame, area: Box?, maxWidth: Int): ByteArray
    /** A small JPEG of f with a tap (from) or a swipe (from -> to) marked on it. */
    fun markedShot(f: Frame, from: Pt, to: Pt?, maxWidth: Int): ByteArray
    /** Saves a picture where you can see it (Pictures/Hunt); returns where. */
    fun savePicture(png: ByteArray, name: String): String
    /** The screenshot with every area the hunter reads or taps drawn on it. */
    fun drawLayout(f: Frame, lay: Layout): ByteArray
    fun logEncounter(row: List<String>)
    fun sound(kind: String)  // "found", "paused", "resumed"
    /** The battle's Mute button is at mute (screenshot px): the ON / OFF button goes under it. */
    fun placeButton(mute: Box)
    /** Which phone this is, for the debug webhook: "Google Pixel 7, Android 16". */
    fun label(): String
    fun loadCalibration(key: String): Calibration?
    fun saveCalibration(key: String, cal: Calibration)
}

/** The website (poopihunt.xyz; Remote on Android): what the hunter shows there. */
interface Link {
    fun state(s: JSONObject)
    fun shot(jpg: ByteArray)
    /** The wild Pokemon as the game draws it: {"count", "species", "png"} (base64). */
    fun sprite(s: JSONObject) {}
}

class Stopped : Exception()

/** Switched off in the middle of a battle: it's yours from there. */
class Paused : Exception()

/** The battle ended (it fled, it was caught, ...) while a catch was going. */
class BattleOver : Exception()

fun now() = System.nanoTime() / 1e9

/** The hunt, ported from pokehunt.py: reads each encounter, runs from the
 *  ones not wanted, catches the ones worth a ball, and holds the battle for
 *  you when it finds a wanted one. */
class Hunter(private val phone: Phone, private val cfg: Config, private val watch: Boolean,
             private val debugMode: Boolean = false) {
    /** Worth catching. Debug mode catches every encounter (then saves), for
     *  testing catching. */
    private val notify = cfg.notify + if (debugMode) listOf(mapOf<String, Any>("species" to "any")) else emptyList()

    /** A step of a catch, for following it in the log. */
    private fun trace(msg: String) = Log.print("    $msg")

    @Volatile var running = true
        private set
    @Volatile var stopped = false
    @Volatile var calibrateNext = false

    private var cal = Calibration()
    private var calKey = ""
    private var calSaved = false
    private var lastClick = now()
    private val discord = Discord(cfg.webhook)
    private val debug = Discord(if (cfg.debugWebhook == cfg.webhook) "" else cfg.debugWebhook, quiet = true)

    /** To your webhook, and a copy to the debug one. */
    private fun post(text: String, png: ByteArray? = null) {
        discord.post(text, png)
        report(text, png)
    }

    /** Only to the debug webhook. */
    private fun report(text: String, png: ByteArray? = null) {
        flushLines()  // keep the order
        flushShots()
        debug.post("`${phone.label()}` $text", png)
    }

    // Everything the hunter says goes to the debug webhook too, gathered into
    // one message every few seconds (Discord takes about 30 a minute).
    private val lines = ArrayList<String>()

    private fun flushLines() {
        val batch = synchronized(lines) { lines.toList().also { lines.clear() } }
        val chunk = StringBuilder()
        for (line in batch) {
            if (chunk.length + line.length > 1800) {
                debug.post("`${phone.label()}` ```\n$chunk```")
                chunk.clear()
            }
            chunk.append(line.take(1800)).append('\n')
        }
        if (chunk.isNotBlank()) debug.post("`${phone.label()}` ```\n$chunk```")
    }

    private fun streamLines() {
        Log.sink = { line -> if (line.isNotBlank()) synchronized(lines) { lines.add(line) } }
        Thread {
            while (!stopped) {
                try { Thread.sleep(5000) } catch (e: InterruptedException) { break }
                flushLines()
                flushShots()
            }
            flushLines()
            flushShots()
        }.apply { isDaemon = true }.start()
    }

    companion object {
        const val SAVE_EVERY = 125.0   // s; the game allows one save every 2 minutes
        const val GONE_AFTER = 1.0     // s without the Mute button before an encounter counts as over
        const val SEEN_AFTER = 0.4     // s with the Mute button before it counts as started
        const val TURN_TIMEOUT = 60.0  // s for one turn: a throw with its shakes, or a move
        const val AFK_EVERY = 15 * 60.0  // Roblox kicks after 20 min without input
    }

    // ------------------------------------------------------------ control

    fun toggle() {
        if (running) {
            running = false
            Log.print("\n|| off (tap ON to carry on)")
            phone.sound("paused")
        } else {
            Log.print("\n>> on")
            phone.sound("resumed")
            running = true
        }
        publish()
    }

    /** The website takes over: whatever battle is on waits for its buttons. */
    fun takeOver() {
        if (remote) return
        commands.clear()
        remote = true
        Log.print("\n<< taken over from the website")
        phone.sound("paused")
        publish()
    }

    /** The website hands the hunt back. */
    fun handBack() {
        if (!remote) return
        remote = false
        running = true
        Log.print("\n>> handed back from the website: hunting")
        phone.sound("resumed")
        publish()
    }

    @Volatile private var busy = false  // catching or running: switching off stops it at once

    private fun nap(seconds: Double) {
        if (stopped) throw Stopped()
        if (busy && (!running || remote)) throw Paused()
        Thread.sleep((seconds * 1000).toLong())
        if (stopped) throw Stopped()
        if (busy && (!running || remote)) throw Paused()
    }

    /** Catching or running from a battle; switching off leaves the battle to you. */
    private fun <T> acting(what: () -> T): T? {
        busy = true
        return try {
            what()
        } catch (e: Paused) {
            Log.print(if (remote) "  taken over mid-battle" else "  switched off mid-battle: it's yours")
            report(if (remote) "taken over from the website mid-battle" else "switched off mid-battle")
            null
        } finally {
            busy = false
        }
    }

    /** Text read in area of f, leaving out what's under the ON / OFF button. */
    private fun ocr(f: Frame, area: Box, zoom: Double = 1.0): List<TextHit> {
        val chip = phone.buttonBox()
        val a = f.clip(area)
        return phone.ocr(f, area, zoom).filter { t ->
            chip == null || !(a.l + t.x * a.w).toInt().let { x -> x in chip.l - 4..chip.r + 4 } ||
                !(a.t + t.y * a.h).toInt().let { y -> y in chip.t - 4..chip.b + 4 }
        }
    }

    private fun waitRunning() {
        while (!running && !remote) nap(0.2)
    }

    /** Runs until stopped; an unexpected error restarts the hunt, never ends it. */
    fun run() {
        streamLines()
        while (true) {
            try {
                hunt()
            } catch (e: Exception) {
                // Stop wakes a sleeping hunter with an interrupt: that's a stop, not an error
                if (e is Stopped || stopped) {
                    Log.print("stopped.")
                    report("stopped")
                    Log.sink = null
                    return
                }
                Log.error(e)
                Log.print("! ${e.javaClass.simpleName}: ${e.message} (restarting)")
                discord.post("pokehunt hit an error and restarted: ${e.javaClass.simpleName}: ${e.message}")
                report("error, restarted:\n```\n${e.stackTraceToString().take(1700)}\n```")
                try { nap(5.0) } catch (s: Exception) {
                    Log.sink = null
                    return
                }
            }
        }
    }

    // ------------------------------------------------------------ screen

    private fun keepRobloxInFront() {
        if (!phone.robloxInFront()) {
            Log.say("  (bringing Roblox back to the front)")
            phone.focusRoblox()
        }
    }

    private fun tap(p: Pt) {
        keepRobloxInFront()
        lastClick = now()
        actionShot(p, null)
        phone.tap(p.x, p.y)
    }

    private fun swipe(x0: Double, y0: Double, x1: Double, y1: Double, seconds: Double) {
        actionShot(Pt(x0, y0), Pt(x1, y1))
        phone.swipe(x0, y0, x1, y1, seconds)
    }

    // The newest screenshot, so a tap can show what was on screen just before it
    private var lastFrame: Frame? = null
    private var lastFrameAt = 0.0

    private fun grab(): Frame = phone.grab().also { lastFrame = it; lastFrameAt = now() }

    // Debug mode: every tap and swipe, on the screenshot just before it, with
    // what the hunter was doing; sent 10 to a message every few seconds
    private val shots = ArrayList<Pair<String, ByteArray>>()
    private var actions = 0

    private fun actionShot(from: Pt, to: Pt?) {
        if (!debugMode) return
        val f = lastFrame?.takeIf { now() - lastFrameAt < 1.5 } ?: grab()
        val what = (if (to == null) "tap (${from.x.toInt()}, ${from.y.toInt()})"
                    else "swipe (${from.x.toInt()}, ${from.y.toInt()}) to (${to.x.toInt()}, ${to.y.toInt()})")
        val caption = "#${++actions} ${Log.clock()} $what, ${Log.last().trim()}"
        val jpg = phone.markedShot(f, from, to, 720)
        val full = synchronized(shots) { shots.add(caption to jpg); shots.size >= 10 }
        if (full) flushShots()
    }

    private fun flushShots() {
        val batch = synchronized(shots) { shots.toList().also { shots.clear() } }
        if (batch.isEmpty()) return
        debug.postFiles("`${phone.label()}` before each action:\n" + batch.joinToString("\n") { it.first.take(180) },
                        batch.mapIndexed { i, (_, jpg) -> "action$i.jpg" to jpg })
    }

    private fun tap(t: TextHit) = tap(Pt(t.x, t.y))

    /** The screenshot and, in an encounter, its layout. */
    private fun look(hint: Box?): Pair<Frame, Layout?> {
        val f = grab()
        val key = "${f.width}x${f.height}"
        if (key != calKey) {  // first look, or the phone turned
            calKey = key
            val saved = phone.loadCalibration(key)
            cal = saved ?: Calibration()
            calSaved = saved != null
        }
        val mute = Vision.findMute(f, hint)
        // the ON / OFF button goes under Mute once the battle screen has settled (while it
        // slides in, Mute is higher up: a button put under it then ends up covering it)
        if (mute != null && Vision.menuReady(f, Layout(mute, cal))) phone.placeButton(mute)
        // covering Mute anyway (put under it mid-slide by an older version): well out of the way, two
        // Mute heights lower; once Mute shows on a settled screen the button goes right under it
        else if (mute == null && hint != null && phone.buttonBox()?.overlaps(Box(hint.l, hint.t, hint.r, hint.b + 2 * hint.h)) == true)
            phone.placeButton(Box(hint.l, hint.t, hint.r, hint.b + 2 * hint.h))
        return f to mute?.let { Layout(it, cal) }
    }

    /** Fits the layout to this screen. True if it worked. */
    private fun autoCalibrate(f: Frame, mute: Box): Boolean {
        val fit = Calibrate.fit(f, mute) ?: return false
        val changed = !calSaved || listOf(fit.kx - cal.kx, fit.ky - cal.ky).any { abs(it) > 0.003 } ||
            listOf(fit.dx - cal.dx, fit.dy - cal.dy).any { abs(it) > 2 }
        cal = fit
        calSaved = true
        phone.saveCalibration(calKey, fit)
        if (changed) {
            val what = "calibrated for this $calKey screen: %.3f across, %.3f down, within %.0f px"
                .format(fit.kx, fit.ky, fit.miss)
            Log.print("  $what")
            report(what, phone.drawLayout(f, Layout(mute, fit)))
        }
        if (calibrateNext) {
            calibrateNext = false
            val path = phone.savePicture(phone.drawLayout(f, Layout(mute, fit)), "calibration")
            Log.print("  the layout is drawn on $path: the boxes should sit on the game's")
        }
        return true
    }

    /** All the text on screen, at screen pixels. */
    private fun screenTexts(f: Frame) =
        ocr(f, Box(0, 0, f.width, f.height)).map { TextHit(it.text.trim(), it.x * f.width, it.y * f.height) }

    private fun regionText(f: Frame, lay: Layout, b: Box) =
        ocr(f, lay.box(b)).joinToString(" ") { it.text }

    private fun readEnemy(f: Frame, lay: Layout): Enc {
        // small screen: blow the text up for the OCR
        val zoom = if (lay.s < 1) 1 / lay.s else 1.0
        return Reader.enemy(ocr(f, lay.box(Ref.BOX), zoom), Vision.icons(f, lay))
    }

    private fun trouble(msg: String, f: Frame) {
        val png = phone.png(f, null, 1200)
        Log.say("  ($msg, screenshot: ${phone.savePicture(png, "trouble")})")
        post("pokehunt: $msg.", png)
    }

    // ------------------------------------------------------------ encounters

    private class Read(val enc: Enc, val f: Frame, val lay: Layout)

    /** Waits for the Fight/Run menu and reads the enemy until the reads agree
     *  (Rules.settled). Null once the encounter is over. */
    private fun readEncounter(start0: Box): Read? {
        var hint: Box = start0
        val reads = ArrayList<Enc>()
        var gone: Double? = null
        val start = now()
        var saved = false
        var calAt = 0.0
        while (true) {
            val (f, lay) = look(hint)
            if (lay == null) {
                gone = gone ?: now()
                if (now() - gone > GONE_AFTER) return null
                nap(0.1)
                continue
            }
            gone = null
            hint = lay.mute
            if (!watch) keepRobloxInFront()  // a popup may be covering the enemy's box
            val menu = Vision.menuReady(f, lay)
            val chat = Vision.chatShowing(f, lay)
            val readable = menu && !chat && Vision.boxVisible(f, lay)
            // the chat window over the enemy's box: close it (reading the screen is slow, so only then)
            if (!readable && menu && now() - start > 2 && !watch && now() - chatClosedAt > 3 &&
                closeChat(f, screenTexts(f))) continue
            // first encounter on this screen, or it can't be read: fit the layout again
            if (now() - calAt > 2 && (!calSaved || calibrateNext || (!readable && now() - start > 3))) {
                calAt = now()
                if (autoCalibrate(f, lay.mute)) continue
            }
            if (readable) {
                reads += readEnemy(f, lay)
                if (reads.size > 8) reads.removeAt(0)
                Rules.settled(reads)?.let { return Read(it, f, lay) }
            }
            if (now() - start > 15 && !saved) {
                // never Run from something unread: it might be the one
                val why = if (chat) "chat is covering the enemy's box (close it in Roblox)"
                          else if (menu) "can't read the enemy's box" else "no Fight/Run menu"
                trouble("$why for 15 s, waiting", f)
                saved = true
            }
            if (!watch && now() - lastClick > AFK_EVERY) {  // waiting for you: Roblox kicks at 20 min
                tap(lay.pt(Ref.AFK_SPOT))
                Log.say("  (anti-AFK tap)")
            }
            nap(0.2)
        }
    }

    /** Taps Run (again if the escape failed) until the encounter is over. */
    private fun runAway(lay0: Layout): Boolean {
        var lay = lay0
        var tapped = 0.0
        var gone: Double? = null
        val start = now()
        var f: Frame? = null
        while (now() - start < 20) {
            waitRunning()
            val (img, cur) = look(lay.mute)
            f = img
            if (cur == null) {
                gone = gone ?: now()
                if (now() - gone > GONE_AFTER) return true
            } else {
                gone = null
                lay = cur
                if (Vision.menuReady(img, lay) && now() - tapped > 2.5) {
                    if (tapped > 0) Log.say("  run failed, trying again")
                    tap(lay.pt(Ref.RUN))
                    tapped = now()
                }
            }
            nap(0.15)
        }
        f?.let { trouble("still in the encounter 20 s after tapping Run", it) }
        return false
    }

    private fun closeBag(texts: List<TextHit>) {
        texts.firstOrNull { it.text.equals("close", true) }?.let { tap(it) }
    }

    /** Bag -> tab -> item -> Use, and -> party member when the item asks for
     *  one. The item is searched for (its name typed into the search box, the
     *  keyboard kept hidden), or scrolled to when typing fails. True once used;
     *  on failure the backpack is closed again. */
    private var chatClosedAt = 0.0

    /** Roblox opens its chat window when messages come in, and on a phone it
     *  covers the game (the bag's list, the enemy's box). Closes it with
     *  Roblox's chat button. True if it tapped. */
    private fun closeChat(f: Frame, texts: List<TextHit>): Boolean {
        if (texts.none { "tap here to chat" in it.text.lowercase() } || now() - chatClosedAt < 3) return false
        chatClosedAt = now()
        val button = Vision.chatButton(f)
        if (button == null) {
            trace("the chat window is open over the game, and Roblox's chat button wasn't found")
            return false
        }
        trace("the chat window is open over the game: closing it")
        tap(button)
        return true
    }

    /** Waits for the Fight / Bag / Run menu. A battle that's over throws
     *  BattleOver: out of battle the Bag button's spot is somewhere else on
     *  screen (a tap there can open the chat window). */
    private fun ensureBattle(lay: Layout) {
        val end = now() + 4
        while (now() < end) {
            val f = grab()
            val mute = Vision.findMute(f, lay.mute)
            if (mute != null && Vision.menuReady(f, Layout(mute, cal))) return
            nap(0.4)
        }
        throw BattleOver()
    }

    /** What useItem used last: prefer, when that was a favorite. */
    private var usedItem = ""

    /** prefer: used instead of item, if it's a favorite in that tab. */
    /** inBattle = false: the bag is opening from the MENU already (restorePp). */
    private fun useItem(lay: Layout, tab: String, item: String, target: String? = null, prefer: String? = null,
                        inBattle: Boolean = true): Boolean {
        // the bag's search box would bring up the keyboard, which on a phone covers the list
        phone.keyboardHidden(true)
        try {
            return useItemIn(lay, tab, item, target, prefer, inBattle)
        } finally {
            phone.keyboardHidden(false)
        }
    }

    private fun useItemIn(lay: Layout, tab: String, wanted: String, target: String?, prefer: String?,
                          inBattle: Boolean): Boolean {
        var item = wanted
        // prefer: looked for on the list first, then searched for; decided once ("done")
        var looking = if (prefer != null && Rules.norm(prefer) != Rules.norm(item)) "look" else "done"
        if (inBattle) {
            ensureBattle(lay)
            trace("bag: opening it for $item")
            tap(lay.pt(Ref.BAG))
        }
        val split = lay.pt(Ref.BAG_SPLIT, 0.0).x
        val v = lay.s * lay.cal.ky  // screen px per reference px, down
        var step = "tab"
        var rowY = 0.0
        var tabAt = 0.0
        var tabHit: TextHit? = null
        var lastRowY: Double? = null  // where the row was on the screenshot before
        var pickedAt = 0.0
        var picks = 0
        var searched = false
        val scroll = Scroll()
        var texts = listOf<TextHit>()
        var f: Frame? = null
        var end = now() + 12
        val openedAt = now()
        var missing = 0  // screenshots in a row without the bag
        var reopened = false
        while (now() < end) {
            nap(0.4)
            val img = grab()
            f = img
            texts = screenTexts(img)
            // the bag can close under it (a chat window, a stray tap): notice,
            // reopen it once, rather than look through a bag that isn't there
            if (closeChat(img, texts)) {
                end = maxOf(end, now() + 3)
                continue
            }
            val bagUp = texts.any { it.text.equals("backpack", true) } ||
                Reader.findTab(texts, "Pokeballs", split, img.width) != null
            missing = if (bagUp) 0 else missing + 1
            if (step in listOf("tab", "item") && missing >= 3 && now() - openedAt > 2) {
                if (reopened || !inBattle || !Vision.menuReady(img, lay)) {
                    trace("bag: it isn't open anymore" + if (texts.any { "tap here to chat" in it.text.lowercase() })
                        " (the chat window is open)" else "")
                    break
                }
                trace("bag: it closed, opening it again")
                tap(lay.pt(Ref.BAG))
                reopened = true
                missing = 0
                step = "tab"
                continue
            }
            when (step) {
                "tab" -> Reader.findTab(texts, tab, split, img.width)?.let {
                    trace("bag: $tab tab at (${it.x.toInt()}, ${it.y.toInt()})" +
                         if (texts.any { t -> t === it }) "" else ", worked out from the tabs around it")
                    tap(it)
                    step = "search"
                    tabAt = now()
                    tabHit = it
                }
                // the search box, right of the list's names: typing the item's name
                // leaves just its row (scrolling for it is the way when that fails)
                "search" -> if (now() - tabAt > 0.6) {
                    val close = texts.firstOrNull { it.text.equals("close", true) }
                    val count = Regex("x[\\d,.]+", RegexOption.IGNORE_CASE)
                    val rows = texts.filter { count.matches(it.text) }.minOfOrNull { it.y }
                    val box = texts.filter {
                        it.x > split && it !== close && !count.matches(it.text) &&
                            (close == null || it.y > close.y + 20 * v) && (rows == null || it.y < rows - 20 * v)
                    }.minByOrNull { it.y }
                    if (looking != "done") {
                        // favorites are at the top, but more than a screenful hides some: then it's searched for
                        val p = prefer!!
                        val fav = texts.filter { Rules.sameWord(p, it.text) && it.x < split }.minByOrNull { it.y }
                        val listed = rows != null
                        when {
                            fav != null -> {
                                looking = "done"
                                if (Vision.favorited(img, texts, fav.y, lay)) {
                                    Log.print("  $p is a favorite: throwing that instead of the $item")
                                    item = p
                                    step = "item"  // it's on the list: no search
                                    tabAt = now()
                                    searched = true
                                }
                                continue
                            }
                            // every favorite is in sight above a plain row, and none is prefer
                            looking == "look" && listed && Vision.plainRowInSight(img, texts, split, lay) -> {
                                looking = "done"
                                continue
                            }
                            looking == "look" && listed && box != null -> {
                                tap(box)
                                nap(0.5)
                                if (phone.typeText(p)) {
                                    trace("bag: looking for a favorited $p")
                                    looking = "searched"
                                } else looking = "done"
                                tabAt = now()
                                continue
                            }
                            looking == "searched" && now() - tabAt > 2.0 -> looking = "done"  // none in the bag
                            looking == "look" && now() - tabAt > 3.0 -> looking = "done"  // no list read: go on without it
                            else -> continue  // the list isn't up yet
                        }
                    }
                    if (box != null) {
                        tap(box)
                        nap(0.5)
                        val typed = phone.typeText(item) || run { nap(0.6); phone.typeText(item) }
                        trace(if (typed) "bag: searching for $item" else "bag: couldn't type into the search box: scrolling")
                        searched = typed
                        step = "item"
                        tabAt = now()
                    } else if (now() - tabAt > 2.5) {
                        trace("bag: no search box seen: scrolling")
                        step = "item"
                    }
                }
                "item" -> {
                    // exact names: "Dive Ball" and "Dream Ball" are one misread apart
                    val row = texts.filter { Rules.norm(it.text) == Rules.norm(item) && it.x < split }
                        .minWithOrNull(compareBy({ it.y }, { it.x }))  // the list row, not the description under it
                    if (row != null && (lastRowY == null || abs(row.y - lastRowY!!) > 6 * v)) {
                        // a list still gliding from a swipe moves rows under the finger,
                        // and a tap only stops the glide: tap once the row holds still
                        lastRowY = row.y
                    } else if (row != null) {
                        rowY = row.y
                        val count = texts.firstOrNull {
                            Regex("x[\\d,.]+", RegexOption.IGNORE_CASE).matches(it.text) && abs(it.y - rowY) < 30 * v
                        }
                        // a tap that didn't select it: try another part of the row
                        val spot = when {
                            picks == 0 || count == null -> Pt(row.x, row.y)
                            picks == 1 -> Pt(count.x, row.y)
                            else -> Pt((row.x + count.x) / 2, row.y)
                        }
                        trace("bag: tapping $item (${count?.text ?: "?"} in the bag) at (${spot.x.toInt()}, ${spot.y.toInt()})")
                        tap(spot)
                        step = "use"
                        pickedAt = now()
                    } else if (now() - tabAt > 1.5 && now() > scroll.at) {
                        lastRowY = null
                        if (!searched) {
                            searched = true
                            // a search left in the box hides everything else (this one
                            // couldn't be typed over)
                            texts.firstOrNull {
                                it.x > split && it.text == it.text.uppercase() && Regex("[A-Z]{2}").containsMatchIn(it.text)
                            }?.let { Log.say("  (the bag's search box says \"${it.text}\": clear it in Roblox, it hides the rest)") }
                        }
                        // not in sight: scroll the list, top to bottom
                        val names = texts.filter { it.x < split }.map { it.text }.toSet()
                        if (!scroll.next(tabHit, texts, split, names)) {
                            trace("bag: looked through $tab, no $item")
                            break
                        }
                        end = maxOf(end, now() + 4)  // scrolling takes a while
                    }
                }
                "use" -> {
                    val use = Vision.findUseButton(img, rowY)
                        ?: texts.firstOrNull { it.text.equals("use", true) && it.y > rowY }?.let { Pt(it.x, it.y) }
                    // the panel over Use names what's selected: never use anything else
                    // (a tap that didn't select leaves the last item selected)
                    val named = use?.let { u -> texts.filter { u.y - it.y in 0.0..140 * v && it.x < u.x } } ?: emptyList()
                    if (use != null && named.any { Rules.norm(it.text) == Rules.norm(item) }) {
                        trace("bag: $item is selected: Use")
                        tap(use)
                        usedItem = item
                        if (target == null) return true
                        step = "target"
                        end = maxOf(end, now() + 6)  // the party screen takes a moment to come up
                    } else if (named.isNotEmpty() || now() - pickedAt > 2.5) {
                        val shown = named.minByOrNull { it.y }?.text ?: "nothing"
                        if (++picks > 3) {
                            trace("bag: $shown is selected, not $item: giving up")
                            break
                        }
                        trace("bag: $shown is selected, not $item: picking it again")
                        step = "item"
                        lastRowY = null
                    }
                }
                "target" -> if (texts.any { it.text.lowercase().startsWith("choose a pokemon") }) {
                    texts.firstOrNull { Rules.sameWord(target!!, it.text) }?.let {
                        trace("bag: giving it to $target")
                        tap(it)
                        return true
                    }
                }
            }
        }
        val shot = f?.let { phone.savePicture(phone.png(it, null, 1200), "bag") } ?: "-"
        val read = texts.joinToString(", ") { it.text }
        Log.say("  (couldn't use $item: $shot, text read: ${read.take(300)})")
        report("couldn't use $item (step: $step). Read: ${read.take(1200)}", f?.let { phone.png(it, null, 1200) })
        closeBag(texts)
        return false
    }

    /** Scrolling the backpack's list: first to the top, then down a page at a
     *  time until the page stops changing. The list's height is taken from
     *  the tab buttons beside it (Recovers ... Z-Crystals). */
    private inner class Scroll {
        var at = 0.0
        private var atTop = false
        private var last: Set<String>? = null
        private var pages = 0

        fun next(tab: TextHit?, texts: List<TextHit>, split: Double, names: Set<String>): Boolean {
            tab ?: return false
            // the list's height from the bag's own tab labels (not a chat window's lines)
            val column = texts.filter { t ->
                abs(t.x - tab.x) < 0.15 * (split - tab.x) &&
                    Reader.TABS.any { Rules.norm(it) == Rules.norm(t.text) || Rules.sameWord(it, t.text) }
            }.map { it.y } + tab.y
            val top = column.minOrNull() ?: return false
            val bottom = column.maxOrNull() ?: return false
            if (bottom - top < 50) return false
            val x = (tab.x + split) / 2
            val up = top + 0.3 * (bottom - top)
            val down = top + 0.8 * (bottom - top)
            if (!atTop) {
                trace("bag: not in sight, scrolling to the top")
                repeat(3) { swipe(x, up, x, down, 0.25) }  // fast: fling to the top
                atTop = true
            } else {
                // the list rows on this page (not the tabs); a page that's
                // mostly the same as the last one is the bottom
                val rows = texts.filter { it.x > tab.x + 0.15 * (split - tab.x) && it.x < split }
                    .map { Rules.norm(it.text) }.filter { it.length > 2 }.toSet()
                val before = last
                if (before != null && rows.isNotEmpty() &&
                    rows.count { it in before } >= 0.7 * minOf(rows.size, before.size)) return false
                if (++pages > 12) return false
                last = rows
                trace("bag: next page")
                swipe(x, down, x, up, 0.7)  // slow: about half a page, no fling
            }
            at = now() + 0.8  // look again once it has settled
            return true
        }
    }

    private var movePP: Int? = null  // the PP useMove saw before using it

    /** Fight -> move. "used", or "no pp" (it goes Back to the menu then). */
    private fun useMove(lay: Layout, move: String): String {
        ensureBattle(lay)
        tap(lay.pt(Ref.FIGHT))
        val v = lay.s * lay.cal.ky
        val end = now() + 6
        while (now() < end) {
            nap(0.4)
            val img = grab()
            val texts = screenTexts(img)
            if (closeChat(img, texts)) continue
            val hit = texts.firstOrNull { it.text.equals(move, true) } ?: continue
            // the moves on this row, left to right, and their "PP / max" under them
            val row = texts.filter { abs(it.y - hit.y) < 15 * v && !Regex("\\d").containsMatchIn(it.text) }
                .map { it.x }.sorted()
            val pps = texts.sortedBy { it.x }.filter { it.y - hit.y > 20 * v && it.y - hit.y < 120 * v }
                .flatMap { t -> Reader.PP.findAll(t.text).map { it.groupValues[1].toInt() } }
            // one PP per move, or they can't be told apart (a slash misread, two read as one)
            val i = row.indexOf(hit.x).takeIf { pps.size == row.size } ?: -1
            if (i in pps.indices && pps[i] == 0) {
                trace("fight: $move has no PP left, back")
                texts.firstOrNull { it.text.equals("back", true) }?.let { tap(it) }
                return "no pp"
            }
            trace("fight: $move" + if (i in pps.indices) " (${pps[i]} PP)" else "")
            movePP = if (i in pps.indices) pps[i] else null
            tap(hit)
            return "used"
        }
        trace("fight: didn't find $move in the move menu")
        return "no menu"
    }

    /** The battle's text after a catch: "Gotcha! ... was caught!", or only
     *  "Rattata was added to the Party." when the first message went by too fast. */
    private fun caughtSaid(msg: String) = msg.lowercase().let {
        "was caught" in it || "gotcha" in it || "added to the party" in it || Regex("sent to (the )?(pc|box)").containsMatchIn(it)
    }

    private class Turn(val outcome: String, val f: Frame?, val lay: Layout, val msgs: List<String>)

    /** After an action, waits until the turn is over: "caught", "turn" (the
     *  menu is back), "over" (the battle ended without a catch) or "timeout". */
    private fun waitTurn(lay0: Layout): Turn {
        var lay = lay0
        val start = now()
        var seen = false
        var gone: Double? = null
        val msgs = ArrayList<String>()
        var f: Frame? = null
        while (now() - start < TURN_TIMEOUT) {
            nap(0.25)
            val (img, cur) = look(lay.mute)
            f = img
            if (cur == null) {  // backpack or party screen still up, or the battle is over
                if (seen) {
                    gone = gone ?: now()
                    if (now() - gone > 4) {
                        val caught = msgs.any { caughtSaid(it) }
                        return Turn(if (caught) "caught" else "over", img, lay, msgs)
                    }
                }
                continue
            }
            seen = true
            gone = null
            lay = cur
            val msg = regionText(img, cur, Ref.MSG)
            if (msg.isNotEmpty() && (msgs.isEmpty() || msgs.last() != msg)) msgs.add(msg)
            val m = msg.lowercase()
            if (caughtSaid(m)) return Turn("caught", img, cur, msgs)
            if (now() - start > 1.5 && Vision.menuReady(img, cur) && "should" in m)
                return Turn("turn", img, cur, msgs)
        }
        return Turn("timeout", f, lay, msgs)
    }

    /** Catches with the plan from the config's "catch": a first-turn ball,
     *  then Yawn, Protect while it gets drowsy, and the sleep ball once it's
     *  asleep; Timer Balls instead once timer_after turns have gone by.
     *  Statused targets (Yawn can't work) get the other ball. Heals and
     *  restores PP along the way. Returns (result, details): "caught", "gave
     *  up", "no bag" (couldn't use the bag), "over" or "timeout". */
    private fun catch(enc: Enc, f0: Frame, lay0: Layout): Pair<String, String> {
        var balls = 0
        var turns = 0
        var last = ""
        return try {
            catching(enc, f0, lay0) { b, t, ball -> balls = b; turns = t; last = ball }
        } catch (e: BattleOver) {
            if (last == "Park Ball") return "caught" to "a Park Ball"  // it never misses: the battle ending is the catch
            Log.print("  the battle is over")
            "over" to "$balls ball(s), $turns turns"
        }
    }

    /** catch()'s turns; progress gets the balls thrown, turns so far and the last ball. */
    private fun catching(enc: Enc, f0: Frame, lay0: Layout, progress: (Int, Int, String) -> Unit): Pair<String, String> {
        var f = f0
        var lay = lay0
        val lead = Regex("should (.+?) do").find(regionText(f, lay, Ref.MSG))?.groupValues?.get(1) ?: ""
        val legendary = Rules.fieldOk("legendary", enc.species)  // much harder to catch
        val maxBalls = cfg.num(if (legendary) "legendary_max_balls" else "max_balls").toInt()
        val maxTurns = cfg.num(if (legendary) "legendary_max_turns" else "max_turns").toInt()
        val balls = ArrayList<String>()
        var turns = 0
        var drowsy = false
        var heals = 0
        var cur = enc
        // the first ball is a Park Ball whenever one is favorited in the bag
        var park = if (cfg.flag("park_ball")) "Park Ball" else null
        var timer = cfg.str("timer_ball")
        Log.print("  catching ${enc.title()} Lv${enc.level.ifEmpty { "?" }} with ${lead.ifEmpty { "?" }}: up to " +
                  "$maxBalls balls, $maxTurns turns" + if (legendary) " (legendary)" else "")
        while (true) {
            val hp = Reader.hp(regionText(f, lay, Ref.PLAYER_BOX))
            Log.print("  turn ${turns + 1}: ${lead.ifEmpty { "ours" }} at ${hp?.let { "${(it * 100).toInt()}%" } ?: "? HP"}, " +
                      "${cur.species} ${cur.status.ifEmpty { "no status" }}" + if (drowsy) ", drowsy" else "")
            var ball: String? = null
            if (turns == 0 && (park != null || cfg.str("first_ball").isNotEmpty())) {
                ball = cfg.str("first_ball").ifEmpty { cfg.str("awake_ball") }  // unless a Park Ball is favorited
            } else if (hp != null && hp < cfg.num("heal_below") && heals < 5) {
                trace("low HP: ${cfg.str("heal_item")}")
                if (!useItem(lay, "Recovers", cfg.str("heal_item"), lead)) return "no bag" to "${cfg.str("heal_item")} failed"
                heals++
            } else if (cur.status == "SLP") {
                ball = cfg.str("asleep_ball")
            } else if (cur.status.isNotEmpty()) {
                ball = cfg.str("awake_ball")  // poisoned etc.: Yawn can't put it to sleep
            } else if (!Rules.sameWord(cfg.str("sleep_with"), lead) || ppFor.isNotEmpty()) {
                ball = cfg.str("awake_ball")  // only the Yawn user puts it to sleep, with PP left; others just throw
            } else {
                val move = cfg.str(if (drowsy) "guard_move" else "sleep_move")
                val used = useMove(lay, move)
                if (used == "used") {
                    drowsy = !drowsy
                } else {
                    if (used == "no pp") needPp(lead, move)
                    ball = cfg.str("awake_ball")
                }
            }
            var plain: String? = null
            if (ball != null && timer.isNotEmpty() && turns >= cfg.num("timer_after") && ball != "Park Ball") {
                plain = ball
                ball = timer
            }
            if (ball != null) {
                if (balls.size >= maxBalls) return "gave up" to "${balls.size} balls"
                trace("throwing a $ball (#${balls.size + 1})")
                var thrown = useItem(lay, "Pokeballs", ball, prefer = park)
                if (!thrown && plain != null) {
                    trace("no $ball to throw: ${plain}s from now on")
                    timer = ""
                    ball = plain
                    thrown = useItem(lay, "Pokeballs", ball, prefer = park)
                }
                if (!thrown) return "no bag" to "$ball failed"
                balls.add(usedItem)
                park = null
                progress(balls.size, turns, usedItem)
            }
            val turn = waitTurn(lay)
            lay = turn.lay
            turns++
            progress(balls.size, turns, balls.lastOrNull() ?: "")
            Log.print("  turn $turns: ${turn.outcome}" +
                      if (turn.msgs.isEmpty()) "" else ". The battle said: ${turn.msgs.joinToString(" / ")}")
            if (debugMode) turn.f?.let { report("catch turn $turns: ${turn.outcome}", gameShot(it, turn.lay)) }
            val details = "${balls.size} ball(s), last ${balls.lastOrNull() ?: "-"}, $turns turns"
            if (balls.lastOrNull() == "Park Ball" && turn.outcome in listOf("caught", "over"))
                return "caught" to "a Park Ball"  // it never misses: the battle ending is the catch
            if (turn.outcome != "turn" || turn.f == null) return turn.outcome to details
            f = turn.f
            if (turn.msgs.any { "fell asleep" in it.lowercase() }) drowsy = false
            if (turns >= maxTurns) return "gave up" to details
            if (Vision.chatShowing(f, lay) || !Vision.boxVisible(f, lay)) {
                trace("can't see its box this turn")
                nap(1.0)  // read again next turn; keep going with what we know
                continue
            }
            val again = readEnemy(f, lay)
            if (again.species.isNotEmpty() && !Rules.sameMon(again, enc)) return "caught" to details  // already the next encounter
            cur = again
        }
    }

    /** MENU -> Save -> dismiss the message -> close MENU, between
     *  encounters. True if the game saved. */
    private val MENU_ITEMS = listOf("Pokedex", "Party", "Bag", "PokeBoosters", "Save", "Trainer Card", "Codes")

    /** The MENU panel is open: two of its labels are read (on a phone they're
     *  small, and one is often misread). */
    private fun menuOpen(texts: List<TextHit>) = texts.count { t ->
        MENU_ITEMS.any { Rules.norm(it) == Rules.norm(t.text) || Rules.sameWord(it, t.text) }
    } >= 2

    private fun menuTab(texts: List<TextHit>) = texts.firstOrNull { Rules.norm(it.text) == "menu" }

    /** Where to tap for a MENU item: its tile, half a row above its label
     *  (on a phone only the tile takes taps); the label itself on the retry,
     *  or if no other label shows how far apart the rows are. */
    private fun menuButton(hit: TextHit, texts: List<TextHit>, retry: Boolean): Pt {
        if (retry) return Pt(hit.x, hit.y)
        val rows = texts.filter { t -> t !== hit && MENU_ITEMS.any { Rules.norm(it) == Rules.norm(t.text) } }
            .map { abs(it.y - hit.y) }.filter { it > 0 }
        val row = rows.minOrNull() ?: return Pt(hit.x, hit.y)
        return Pt(hit.x, hit.y - row / 2)
    }

    private fun saveGame(): Boolean {
        var f = grab()
        var texts = screenTexts(f)
        val tab = menuTab(texts)
        if (tab == null || Vision.findMute(f) != null) return false
        trace("save: MENU at (${tab.x.toInt()}, ${tab.y.toInt()})")
        tap(tab)
        var step = "save"
        var saved = false
        var shown = false
        var savedAt = 0.0
        var tries = 0
        val end = now() + 20
        while (now() < end) {
            nap(0.4)
            f = grab()
            texts = screenTexts(f)
            if (closeChat(f, texts)) continue
            when (step) {
                "save" -> {
                    if (Vision.findMute(f) != null) return false  // an encounter came first
                    val hit = texts.firstOrNull { Rules.norm(it.text) == "save" }
                    if (hit != null && menuOpen(texts)) {
                        val at = menuButton(hit, texts, retry = tries > 0)
                        trace("save: tapping Save at (${at.x.toInt()}, ${at.y.toInt()})")
                        tap(at)
                        step = "message"
                        savedAt = now()
                    }
                }
                "message" -> {
                    val msg = texts.firstOrNull { saveMessage(it.text) }
                    if (msg != null) {
                        if (!shown) trace("save: the game says \"${msg.text}\"")
                        // The text types itself out: a tap while it's typing only
                        // finishes it, so keep tapping until the box is gone.
                        saved = saved || "successful" in msg.text.lowercase()
                        SAVE_WAIT.find(msg.text)?.let { saveWait = it.groupValues[1].toInt() }
                        shown = true
                        tap(msg)
                        nap(0.6)
                    } else if (shown) {  // the message was there and is gone now
                        step = "close"
                    } else if (now() - savedAt > 4 && tries++ < 2) {  // the tap on Save didn't take
                        trace("save: no message yet, tapping Save again")
                        step = "save"
                    }
                }
            }
            when (step) {
                "close" -> if (menuOpen(texts)) {  // the menu panel comes back; its tab closes it
                    menuTab(texts)?.let {
                        trace("save: closing the menu")
                        tap(it)
                        step = "closed"
                    }
                }
                "closed" -> if (!menuOpen(texts)) return saved
            }
        }
        val png = phone.png(f, null, 1200)
        Log.say("  (saving got stuck at \"$step\": ${phone.savePicture(png, "save")})")
        report("saving got stuck at \"$step\". Read: ${texts.joinToString(", ") { it.text }.take(1200)}", png)
        return saved
    }

    /** The box after Save: "Save successful", "Try again", or "Unable to
     *  save. Please wait 95 more seconds." (one save every 2 minutes). */
    private fun saveMessage(text: String): Boolean {
        val t = text.lowercase()
        return ("save" in t && ("successful" in t || "again" in t || "unable" in t)) || SAVE_WAIT.containsMatchIn(t)
    }

    private val SAVE_WAIT = Regex("wait\\s+(\\d+)\\s+more\\s+second", RegexOption.IGNORE_CASE)
    private var saveWait = 0  // s the game said to wait before saving again

    /** The Pokemon whose moves ran out of PP: the PP item, once the battle is over. */
    @Volatile private var ppFor = ""

    /** A move is out of PP. The game takes the PP item only outside battles,
     *  so it's used between encounters (restorePp); balls until then. */
    private fun needPp(lead: String, move: String) {
        if (ppFor.isNotEmpty()) return
        ppFor = lead.ifEmpty { cfg.str("sleep_with") }
        Log.print("  $move is out of PP: a ${cfg.str("pp_item")} on $ppFor after this battle " +
                  "(the game takes it only outside battles)")
    }

    /** MENU -> Bag -> Recovers -> the PP item -> Use -> the Pokemon, between
     *  encounters, then the bag and the menu closed again. lay: the last
     *  battle's, for where the bag's parts are. True once used. */
    private fun restorePp(lay: Layout): Boolean {
        var f = grab()
        var texts = screenTexts(f)
        val tab = menuTab(texts)
        if (tab == null || Vision.findMute(f) != null) return false
        trace("pp: MENU at (${tab.x.toInt()}, ${tab.y.toInt()})")
        tap(tab)
        val end = now() + 8
        while (now() < end) {
            nap(0.4)
            f = grab()
            texts = screenTexts(f)
            if (closeChat(f, texts)) continue
            if (Vision.findMute(f) != null) return false  // an encounter came first
            val hit = texts.firstOrNull { Rules.norm(it.text) == "bag" }
            if (hit != null && menuOpen(texts)) {
                val at = menuButton(hit, texts, retry = false)
                trace("pp: tapping Bag at (${at.x.toInt()}, ${at.y.toInt()})")
                tap(at)
                val used = useItem(lay, "Recovers", cfg.str("pp_item"), ppFor, inBattle = false)
                closeAfterItem()
                return used
            }
        }
        Log.say("  (couldn't open the Bag from the MENU: ${phone.savePicture(phone.png(f, null, 1200), "pp-menu")})")
        clearLeftovers()
        return false
    }

    /** After an item outside battle: its message tapped away, the bag's
     *  Close, the MENU's tab, until neither is up. */
    private fun closeAfterItem() {
        val end = now() + 10
        while (now() < end) {
            nap(0.5)
            val texts = screenTexts(grab())
            val msg = texts.firstOrNull { "restored" in it.text.lowercase() || "recovered" in it.text.lowercase() }
            val close = texts.firstOrNull { it.text.equals("close", true) }
            when {
                msg != null -> tap(msg)
                close != null -> tap(close)
                menuOpen(texts) -> menuTab(texts)?.let { tap(it) }
                else -> return
            }
        }
    }

    /** Closes a save message or menu panel left open; no encounters come
     *  while either is up. True if it found something to close. */
    private fun clearLeftovers(): Boolean {
        val texts = screenTexts(grab())
        texts.firstOrNull { saveMessage(it.text) }?.let {
            tap(it)
            return true
        }
        if (menuOpen(texts)) {
            menuTab(texts)?.let {
                tap(it)
                return true
            }
        }
        return false
    }

    /** Taps an empty spot every few seconds until you're back (you touch the
     *  screen) or the encounter is over. Then pauses the hunt. */
    private fun keepAlive(lay: Layout) {
        val since = now()
        while (running && !remote) {
            tap(lay.pt(Ref.IDLE_SPOT))
            val until = now() + cfg.keepaliveSeconds
            var back = false
            while (now() < until && running && !remote) {
                nap(0.1)
                if (phone.touchedAt() > since) { back = true; break }
            }
            if (back) {
                Log.print("  you're back: stopped tapping.")
                break
            }
            if (!running || remote) break
            var gone = 0
            while (gone < 3 && Vision.findMute(grab(), lay.mute) == null) {
                gone++
                nap(0.4)
            }
            if (gone < 3) continue
            Log.print("  the encounter ended.")
            break
        }
        if (running && !remote) {
            running = false
            phone.sound("paused")
            Log.print("|| off. Tap ON to hunt again.")
        }
    }

    private fun logEncounter(enc: Enc, action: String) {
        phone.logEncounter(listOf(enc.species, enc.level, if (enc.shiny) "1" else "0", if (enc.tint) "1" else "0",
                                  enc.reskin, enc.gradient, action))
    }

    private fun gameShot(f: Frame, lay: Layout) = phone.png(f, lay.box(Ref.PANEL), 1200)

    private fun found(enc: Enc, f: Frame, lay: Layout) {
        val shot = gameShot(f, lay)
        phone.savePicture(shot, Rules.norm(enc.species))
        post("${cfg.ping} ${enc.title()}".trim(), shot)
        phone.sound("found")
    }

    private fun waitGone(lay: Layout) {
        var gone: Double? = null
        while (true) {
            if (Vision.findMute(grab(), lay.mute) != null) gone = null
            else {
                gone = gone ?: now()
                if (now() - gone > GONE_AFTER) return
            }
            nap(0.2)
        }
    }

    // ------------------------------------------------------------ the website

    /** The website, when this hunt is linked to it. */
    @Volatile var link: Link? = null
    /** Taken over from the website: battles wait for its buttons. */
    @Volatile var remote = false
        private set
    /** The website's battle buttons ({"cmd": "ball", "name": "Timer Ball", "seq": 3}), in order. */
    val commands = LinkedBlockingQueue<JSONObject>()
    /** The page asked for a new picture. */
    @Volatile var wantShot = false

    // what the page shows
    private var encounters = 0
    @Volatile private var webPhase = "waiting"  // or "battle"
    @Volatile private var webEnc: Enc? = null
    @Volatile private var webWanted = false
    @Volatile private var webLead = Lead()
    @Volatile private var webMoves = listOf<Move>()
    @Volatile private var webMsg = ""      // what the battle said last turn
    @Volatile private var webActing = ""   // what it's doing for the page right now
    @Volatile private var webError = ""
    @Volatile private var webDone = 0      // the last command done
    private val knownMoves = HashMap<String, List<Move>>()  // by lead

    private fun publish() {
        val l = link ?: return
        val s = JSONObject().put("hunting", true).put("running", running).put("remote", remote)
            .put("phase", webPhase).put("count", encounters).put("wanted", webWanted)
            .put("msg", webMsg).put("acting", webActing).put("error", webError).put("done", webDone)
            .put("balls", JSONArray(cfg.webBalls)).put("heal_item", cfg.str("heal_item")).put("pp_item", cfg.str("pp_item"))
            .put("moves", JSONArray(webMoves.map {
                JSONObject().put("name", it.name).put("pp", it.pp ?: JSONObject.NULL).put("max", it.max ?: JSONObject.NULL)
            }))
            .put("log", JSONArray(Log.recent().lines().filter {
                it.isNotBlank() && !it.startsWith("  wanted:") && !it.startsWith("  auto-catch:") && !it.startsWith("  post only:")
            }.takeLast(8)))
        webEnc?.let { e ->
            s.put("enc", JSONObject().put("species", e.species).put("level", e.level).put("shiny", e.shiny)
                .put("tint", e.tint).put("reskin", e.reskin).put("gradient", e.gradient).put("status", e.status))
        }
        val lead = webLead
        if (lead != Lead()) s.put("lead", JSONObject().put("name", lead.name).put("level", lead.level ?: JSONObject.NULL)
            .put("hp", lead.hp ?: JSONObject.NULL).put("max", lead.max ?: JSONObject.NULL))
        l.state(s)
    }

    /** The wild Pokemon's sprite area, for the website (pokehunt.py send_sprite). */
    private fun sendSprite(f: Frame, lay: Layout, enc: Enc) {
        val l = link ?: return
        runCatching {  // the page has the plain sprite: never worth stopping the hunt for
            val png = phone.png(Vision.spriteArea(f, lay), null, 96)
            l.sprite(JSONObject().put("count", encounters).put("species", enc.species)
                .put("png", java.util.Base64.getEncoder().encodeToString(png)))
        }
    }

    private fun sendShot(f: Frame, lay: Layout) {
        link?.shot(phone.jpg(f, lay.box(Ref.PANEL), 960))
    }

    /** Reads your Pokemon's box for the page; a box half covered (the
     *  level-up stats panel) leaves the last whole reading. */
    private fun readLead(f: Frame, lay: Layout) {
        val zoom = if (lay.s < 1) 1 / lay.s else 1.0
        val lead = Reader.lead(ocr(f, lay.box(Ref.PLAYER_BOX), zoom))
        if (lead.complete) webLead = lead
    }

    /** Fight -> reads the moves and their PP -> Back to Fight / Bag / Run. */
    private fun readMoves(lay: Layout): List<Move> {
        ensureBattle(lay)
        tap(lay.pt(Ref.FIGHT))
        val zoom = if (lay.s < 1) 1 / lay.s else 1.0
        var moves = emptyList<Move>()
        val end = now() + 5
        while (now() < end && moves.isEmpty()) {
            nap(0.4)
            val img = grab()
            if (Vision.menuReady(img, lay)) continue  // the Fight menu isn't up yet
            lastMenu = ocr(img, lay.box(Ref.MSG), zoom)
            moves = Reader.moves(lastMenu)
        }
        trace("fight: moves " + moves.joinToString(", ") { "${it.name} ${it.pp ?: "?"}/${it.max ?: "?"}" } +
              if (moves.any { it.pp == null }) " (read: ${lastMenu.joinToString(" | ") { it.text }})" else "")
        val back = now() + 6
        while (now() < back) {
            val img = grab()
            if (Vision.menuReady(img, lay)) break
            val texts = screenTexts(img)
            if (closeChat(img, texts)) continue
            texts.firstOrNull { it.text.equals("back", true) }?.let { tap(it) }
            nap(0.6)
        }
        return moves
    }

    private var lastMenu = listOf<TextHit>()  // the Fight menu's text, for the log

    private fun setMoves(moves: List<Move>) {
        webMoves = moves
        if (webLead.name.isNotEmpty()) knownMoves[webLead.name] = moves
    }

    private fun battleOver(msg: String) {
        Log.print("  $msg")
        webPhase = "waiting"
        webActing = ""
        if (msg.isNotEmpty()) webMsg = msg
        publish()
    }

    /** Taken over from the website: the battle waits for the page's buttons,
     *  and the page sees each turn (a picture, the battle's text, your lead).
     *  Returns once the battle is over or the website hands it back. */
    private fun remoteBattle(start: Read) {
        var f = start.f
        var lay = start.lay
        val enc = start.enc
        readLead(f, lay)
        webMoves = knownMoves[webLead.name] ?: emptyList()
        sendShot(f, lay)
        publish()
        try {
            if (webMoves.isEmpty()) {
                webActing = "Reading your moves"
                publish()
                setMoves(readMoves(lay))
                webActing = ""
                webError = if (webMoves.isEmpty()) "Couldn't read your moves" else ""
                publish()
            }
            var checked = now()
            var gone: Double? = null
            while (remote) {
                val cmd = commands.poll(200, TimeUnit.MILLISECONDS)
                if (stopped) throw Stopped()
                if (cmd == null) {
                    if (wantShot) {
                        wantShot = false
                        sendShot(grab(), lay)
                    }
                    if (now() - checked < 1) continue
                    checked = now()
                    f = grab()
                    val mute = Vision.findMute(f, lay.mute)
                    if (mute == null) {
                        gone = gone ?: now()
                        if (now() - gone > GONE_AFTER) return battleOver("the battle is over")
                    } else {
                        gone = null
                        lay = Layout(mute, cal)
                    }
                    continue
                }
                val what = cmd.optString("cmd")
                val name = cmd.optString("name")
                webError = ""
                webActing = when (what) {
                    "ball" -> "Throwing a $name"
                    "move" -> "Using $name"
                    "heal" -> "Using a ${cfg.str("heal_item")}"
                    "pp" -> "A ${cfg.str("pp_item")} after this battle"
                    "run" -> "Running"
                    else -> ""
                }
                if (webActing.isEmpty()) continue
                Log.print("  website: ${webActing.replaceFirstChar { it.lowercase() }}")
                publish()
                val done = when (what) {
                    "ball" -> name in cfg.webBalls && useItem(lay, "Pokeballs", name)
                    "move" -> when (useMove(lay, name)) {
                        "used" -> {
                            setMoves(webMoves.map { if (it.name == name) it.copy(pp = (movePP ?: it.pp)?.minus(1)) else it })
                            true
                        }
                        "no pp" -> {
                            setMoves(webMoves.map { if (it.name == name) it.copy(pp = 0) else it })
                            webError = "$name has no PP left"
                            false
                        }
                        else -> {
                            webError = "Couldn't find $name in the Fight menu"
                            false
                        }
                    }
                    "heal" -> useItem(lay, "Recovers", cfg.str("heal_item"), webLead.name)
                    "pp" -> {  // the game takes it only outside battles
                        needPp(webLead.name, "a move")
                        webError = "The game takes a ${cfg.str("pp_item")} only outside battles: it's used after this one"
                        false  // no turn taken: the page shows that, and the battle goes on
                    }
                    else -> {  // run: taps Run until the battle is over
                        webDone = cmd.optInt("seq")
                        if (runAway(lay)) return battleOver("got away")
                        webError = "Couldn't get away"
                        false
                    }
                }
                webDone = cmd.optInt("seq")
                if (!done) {
                    if (webError.isEmpty()) webError = "Couldn't: ${webActing.replaceFirstChar { it.lowercase() }} didn't work"
                    webActing = ""
                    grab().let { if (Vision.findMute(it, lay.mute) != null) sendShot(it, lay) }
                    publish()
                    continue
                }
                val turn = waitTurn(lay)
                lay = turn.lay
                webActing = ""
                if (turn.msgs.isNotEmpty()) webMsg = turn.msgs.takeLast(3).joinToString(" ")
                Log.print("  turn: ${turn.outcome}" + if (turn.msgs.isEmpty()) "" else ". The battle said: ${turn.msgs.joinToString(" / ")}")
                turn.f?.let { img ->
                    f = img
                    if (turn.outcome == "turn") {
                        readLead(img, lay)
                        if (!Vision.chatShowing(img, lay) && Vision.boxVisible(img, lay))
                            readEnemy(img, lay).takeIf { it.species.isNotEmpty() && Rules.sameMon(it, enc) }?.let { webEnc = it }
                    }
                    if (Vision.findMute(img, lay.mute) != null) sendShot(img, lay)
                }
                when (turn.outcome) {
                    "caught" -> {
                        logEncounter(enc, "caught")
                        post("Caught from the website: ${enc.title()}", gameShot(start.f, start.lay))
                        saveDueFromWeb = cfg.saveAfterCatch
                        return battleOver("caught it!")
                    }
                    "over" -> return battleOver("the battle is over")
                    "timeout" -> webError = "That turn took over a minute"
                }
                publish()
            }
        } catch (e: BattleOver) {
            battleOver("the battle is over")
        }
    }

    /** A catch made from the website: save the game, the way the hunter's own catches do. */
    private var saveDueFromWeb = false

    private fun hunt() {
        var hint: Box? = null
        var since: Double? = null
        var lastBattle: Enc? = null  // the battle on screen, once read
        var lastEnc = now()
        var lastStatus = now()
        var quietSaid = false
        var saveDue = false
        var lastSave = 0.0
        var lastPp = 0.0
        var ppTries = 0
        var lastCheck = 0.0
        var firstLook = true
        var lastSeen = now()
        Log.print(if (!running) "pokehunt off: tap ON to start." else "pokehunt " + if (watch) "watching (no taps)." else "hunting.")
        report(if (watch) "watching started" else "hunting started")
        if (running && !watch) phone.focusRoblox()
        cfg.targets.forEach { Log.print("  wanted: ${Rules.show(it)}") }
        notify.forEach { Log.print("  auto-catch: ${Rules.show(it)}") }
        if (debugMode) Log.print("  debug mode: catching every encounter, then saving; a screenshot every catch turn")
        cfg.post.forEach { Log.print("  post only: ${Rules.show(it)}") }
        // started in the middle of a battle: that one's yours (the hunter would act on it at once).
        // Screen capture's first pictures are black for a few seconds (MuMu: over 4):
        // wait for real ones, then watch them for 2 s.
        var battle = false
        if (running && !watch) {
            val until = now() + 10
            var lit: Double? = null  // when the first real picture came
            while (!battle && now() < until && (lit == null || now() - lit < 2)) {
                val (f, lay) = look(null)
                if (lit == null && Vision.brightness(f) > 0) lit = now()
                battle = lay != null
                if (!battle) nap(0.3)
            }
        }
        if (battle) {
            running = false
            Log.print("  a battle is on screen, so Hunt is off: tap ON when it should carry on (or take over on the website)")
            phone.sound("paused")
        }
        publish()  // the website sees it at once, not at the first encounter
        while (true) {
            if (!running && !remote) {
                waitRunning()
                if (!watch) phone.focusRoblox()
                since = null
                lastEnc = now(); lastStatus = now()
            }
            val (f, lay) = look(hint)
            if (firstLook) {  // for checking what it sees
                firstLook = false
                Log.print("  sees a ${f.width}x${f.height} screen, ${Vision.brightness(f)}% bright: " +
                          phone.savePicture(phone.png(f, null, 1200), "first-look"))
            }
            if (lay != null) lastSeen = now()
            else if (now() - lastSeen > 30) {
                lastSeen = now()
                Log.print("  (${Log.clock()}: no Mute button in sight on the ${f.width}x${f.height} screen, ${Vision.brightness(f)}% bright)")
            }
            if (now() - lastStatus > 60 * cfg.statusEveryMinutes) {
                post("pokehunt: still hunting, $encounters encounters so far.")
                lastStatus = now()
            }
            if (saveDueFromWeb) {
                saveDueFromWeb = false
                saveDue = true
            }
            if (lay == null) {
                since = null
                lastBattle = null
                if (webPhase != "waiting") {
                    webPhase = "waiting"
                    publish()
                }
                val last = hint
                if (ppFor.isNotEmpty() && last != null && !watch && now() - lastPp > 30) {
                    lastPp = now()
                    ppTries++
                    val who = ppFor
                    if (restorePp(Layout(last, cal))) {
                        Log.say("  (PP restored: a ${cfg.str("pp_item")} on $who)")
                        ppFor = ""
                        ppTries = 0
                        saveDue = saveDue || cfg.saveAfterCatch
                    } else if (ppTries >= 3) {
                        Log.say("  (couldn't use a ${cfg.str("pp_item")} 3 times: do it in Roblox; " +
                                "until then the catches just throw balls)")
                        lastPp = now() + 30 * 60  // and again in half an hour
                        ppTries = 0
                    }
                    continue
                }
                if (saveDue && !watch && now() - lastSave > SAVE_EVERY) {
                    saveWait = 0
                    if (saveGame()) {
                        Log.say("  (game saved)")
                        saveDue = false
                        lastSave = now()
                    } else {  // try again in 20 s, or when the game said it can
                        if (saveWait > 0) Log.say("  (the game says wait $saveWait s to save)")
                        lastSave = now() - SAVE_EVERY + maxOf(20, saveWait + 5)
                    }
                    continue
                }
                Log.say("waiting for an encounter...")
                if (!watch) keepRobloxInFront()  // a popup may be hiding the encounter
                if (!watch && now() - lastEnc > 20 && now() - lastCheck > 20) {
                    lastCheck = now()
                    if (clearLeftovers()) Log.say("  (closed a save message / menu left open)")
                }
                if (hint != null && !watch && now() - lastClick > AFK_EVERY) {
                    tap(Layout(hint, cal).pt(Ref.AFK_SPOT))
                    Log.say("  (anti-AFK tap)")
                }
                val quiet = now() - lastEnc
                if (quiet > 60 * cfg.quietAlertMinutes && !quietSaid) {
                    trouble("no encounter for ${(quiet / 60).toInt()} min", f)
                    quietSaid = true
                }
                nap(0.2)
                continue
            }
            hint = lay.mute
            since = since ?: now()
            if (now() - since < SEEN_AFTER) {
                nap(0.1)
                continue
            }

            val got = readEncounter(lay.mute)
            since = null
            if (got == null) {
                lastBattle = null
                continue
            }
            val enc = got.enc
            // the same battle read again: handed back from the website, or switched on again mid-battle
            val again = lastBattle?.let { Rules.sameMon(it, enc) } ?: false
            lastBattle = enc
            if (!again) {
                encounters++
                lastEnc = now()
                quietSaid = false
            }
            val rule = Rules.wanted(enc, cfg.targets)
            Log.resetSay()
            val catchIt = Rules.wanted(enc, notify) != null
            webPhase = "battle"
            webEnc = enc
            webWanted = rule != null
            webMsg = ""
            webError = ""
            if (!again) sendSprite(got.f, got.lay, enc)
            val action = if (remote) "website" else if (rule != null) "WANTED!" else if (watch) "-" else if (catchIt) "catch" else "run"
            if (!again) Log.print("%s  #%-4d %-55s %s".format(Log.clock(), encounters, enc.describe(), action))
            publish()
            if (remote) {
                if (!again) {
                    logEncounter(enc, if (rule != null) "found" else "website")
                    if (rule != null) found(enc, got.f, got.lay)  // you may not be looking at the page
                }
                remoteBattle(got)
            } else if (rule != null) {
                if (!again) {
                    logEncounter(enc, "found")
                    found(enc, got.f, got.lay)
                }
                if (!watch) keepAlive(got.lay) else waitGone(got.lay)
            } else if (watch) {
                if (!again) logEncounter(enc, "seen")
                waitGone(got.lay)
            } else if (catchIt) {  // worth catching, not stopping
                val (result, details) = acting { catch(enc, got.f, got.lay) } ?: continue
                Log.print("  catch: $result ($details)")
                logEncounter(enc, result)
                if (result == "caught") {
                    saveDue = cfg.saveAfterCatch
                    post("Caught: ${enc.title()} ($details)", gameShot(got.f, got.lay))
                    continue
                }
                if (result == "no bag" || (result == "gave up" && cfg.str("on_fail") == "hold")) {
                    report("couldn't catch alone ($result, $details): holding it for you")
                    found(enc, got.f, got.lay)  // couldn't do it alone: ping, hold the battle for you
                    keepAlive(got.lay)
                    continue
                }
                post("Couldn't catch ($result, $details), ran from: ${enc.title()}", gameShot(got.f, got.lay))
                if (Vision.findMute(grab(), got.lay.mute) != null) acting { runAway(got.lay) }
            } else {
                if (!again && Rules.wanted(enc, cfg.post) != null) {  // worth a look, not a ball
                    Log.say("  (posted to Discord)")
                    post("Seen: ${enc.title()} (ran)", gameShot(got.f, got.lay))
                }
                if (!again) logEncounter(enc, "ran")
                acting { runAway(got.lay) }
            }
        }
    }
}
