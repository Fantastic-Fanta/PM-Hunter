package com.pokehunt

import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.abs

// What's wanted, ported from pokehunt.py. The config is the same JSON as
// config.json on the computer.

/** Your Pokemon in battle, from its box (Ref.PLAYER_BOX): "Clodsire Lv. 109", "350 / 376". */
data class Lead(val name: String = "", val level: Int? = null, val hp: Int? = null, val max: Int? = null) {
    /** All of it read: something over the box (the level-up stats panel) leaves gaps. */
    val complete get() = name.isNotEmpty() && level != null && hp != null && max != null && hp <= max
}

/** A move in the Fight menu, with its PP left and its most. */
data class Move(val name: String, val pp: Int?, val max: Int?)

/** Text read off the screen; x, y as fractions of the area read, top-left origin. */
data class TextHit(val text: String, val x: Double, val y: Double)

data class Enc(val species: String = "", val level: String = "", val reskin: String = "",
               val gradient: String = "", val status: String = "",
               val shiny: Boolean = false, val tint: Boolean = false) {
    operator fun get(field: String): Any = when (field) {
        "species" -> species; "level" -> level; "reskin" -> reskin; "gradient" -> gradient
        "status" -> status; "shiny" -> shiny; "tint" -> tint
        else -> ""
    }

    fun describe(): String {
        val words = listOf(if (shiny) "shiny" else "", species.ifEmpty { "?" },
                           if (level.isNotEmpty()) "Lv$level" else "").filter { it.isNotEmpty() }
        val extras = listOf(if (reskin.isNotEmpty()) "reskin $reskin" else "",
                            if (gradient.isNotEmpty()) "gradient $gradient" else "",
                            if (tint) "tint" else "").filter { it.isNotEmpty() }
        return words.joinToString(" ") + if (extras.isNotEmpty()) "  (${extras.joinToString(", ")})" else ""
    }

    /** Doodle reskin, Mirage gradient, Shiny, Tinted Lapras */
    fun title(): String {
        val traits = listOf(if (reskin.isNotEmpty()) "${titleCase(reskin)} reskin" else "",
                            if (gradient.isNotEmpty()) "${titleCase(gradient)} gradient" else "",
                            if (shiny) "Shiny" else "", if (tint) "Tinted" else "")
            .filter { it.isNotEmpty() }.joinToString(", ")
        return "$traits $species".trim()
    }

    private fun titleCase(s: String) =
        s.lowercase().split(" ").joinToString(" ") { w -> w.replaceFirstChar { it.uppercase() } }
}

object Rules {
    val FIELDS = listOf("species", "shiny", "tint", "reskin", "gradient")

    val LEGENDARIES = """
        Articuno Zapdos Moltres Mewtwo Mew Raikou Entei Suicune Lugia Ho-Oh Celebi
        Regirock Regice Registeel Latias Latios Kyogre Groudon Rayquaza Jirachi Deoxys
        Uxie Mesprit Azelf Dialga Palkia Heatran Regigigas Giratina Cresselia Phione
        Manaphy Darkrai Shaymin Arceus Victini Cobalion Terrakion Virizion Tornadus
        Thundurus Reshiram Zekrom Landorus Kyurem Keldeo Meloetta Genesect Xerneas
        Yveltal Zygarde Diancie Hoopa Volcanion Type:Null Silvally Tapu-Koko Tapu-Lele
        Tapu-Bulu Tapu-Fini Cosmog Cosmoem Solgaleo Lunala Necrozma Magearna Marshadow
        Zeraora Meltan Melmetal Zacian Zamazenta Eternatus Kubfu Urshifu Zarude
        Regieleki Regidrago Glastrier Spectrier Calyrex Enamorus Wo-Chien Chien-Pao
        Ting-Lu Chi-Yu Koraidon Miraidon Okidogi Munkidori Fezandipiti Ogerpon
        Terapagos Pecharunt
    """.trim().split(Regex("\\s+"))
    private val REGIONS = Regex("^(alolan|galarian|hisuian|paldean)\\W*", RegexOption.IGNORE_CASE)

    fun norm(s: Any?) = (s?.toString() ?: "").lowercase().replace(Regex("[^a-z0-9]"), "")

    /** Equal, forgiving one misread letter in longer words. */
    fun sameWord(want: String, got: String): Boolean {
        val a = norm(want).replace('0', 'o')
        val b = norm(got).replace('0', 'o')
        return a == b || (a.length >= 5 && ratio(a, b) >= 0.8)
    }

    /** Python's difflib.SequenceMatcher(None, a, b).ratio() */
    fun ratio(a: String, b: String): Double =
        if (a.isEmpty() && b.isEmpty()) 1.0 else 2.0 * matches(a, b) / (a.length + b.length)

    private fun matches(a: String, b: String): Int {
        var best = 0; var bi = 0; var bj = 0
        for (i in a.indices) for (j in b.indices) {
            var k = 0
            while (i + k < a.length && j + k < b.length && a[i + k] == b[j + k]) k++
            if (k > best) { best = k; bi = i; bj = j }
        }
        if (best == 0) return 0
        return best + matches(a.substring(0, bi), b.substring(0, bj)) +
            matches(a.substring(bi + best), b.substring(bj + best))
    }

    fun fieldOk(want: Any, got: Any): Boolean {
        if (want is Boolean) return want == truthy(got)
        if (want == "any") return truthy(got)
        if (want == "none") return !truthy(got)
        val wants = (if (want is List<*>) want else listOf(want)).flatMap {
            if (it == "legendary") LEGENDARIES else listOf(it.toString())
        }
        val g = got.toString()
        val plain = g.replace(REGIONS, "")  // Galarian-Articuno
        return wants.any { sameWord(it, g) || sameWord(it, plain) }
    }

    private fun truthy(v: Any) = if (v is Boolean) v else v.toString().isNotEmpty()

    fun ruleOk(rule: Map<String, Any>, enc: Enc): Boolean = rule.all { (k, v) ->
        if (k == "not_species") !fieldOk(v, enc.species) else fieldOk(v, enc[k])
    }

    /** The first rule this encounter matches, or null. */
    fun wanted(enc: Enc, rules: List<Map<String, Any>>) = rules.firstOrNull { ruleOk(it, enc) }

    fun show(rule: Map<String, Any>) = rule.entries.joinToString(", ") { (k, v) -> "$k=$v" }

    /** Still the same Pokemon, forgiving a misread letter ("Type: Nulll"). */
    fun sameMon(a: Enc, b: Enc): Boolean {
        if (a.level.isNotEmpty() && b.level.isNotEmpty() && a.level != b.level) return false
        return sameWord(a.species, b.species) && listOf("reskin", "gradient").all { k ->
            val x = a[k].toString(); val y = b[k].toString()
            x.isEmpty() || y.isEmpty() || sameWord(x, y)
        }
    }
}

class Config(json: String) {
    private val o = JSONObject(json)
    val webhook: String = o.optString("webhook", "")
    val ping: String = o.optString("ping", "")
    /** Gets everything (errors, trouble, catches, finds, calibration) for
     *  working out what went wrong. "" turns it off. */
    val debugWebhook: String = o.optString("debug_webhook", DEBUG_WEBHOOK)
    val targets = rules("targets")
    val notify = rules("notify")
    val post = rules("post")
    @Suppress("UNCHECKED_CAST")
    val catch: Map<String, Any> = CATCH_PLAN + (o.optJSONObject("catch")?.let { plain(it) as Map<String, Any> } ?: emptyMap())
    val saveAfterCatch = o.optBoolean("save_after_catch", true)
    val keepaliveSeconds = o.optDouble("keepalive_seconds", 5.0)
    val statusEveryMinutes = o.optDouble("status_every_minutes", 60.0)
    val quietAlertMinutes = o.optDouble("quiet_alert_minutes", 3.0)
    /** The balls the website offers when you battle from it. */
    val webBalls: List<String> = o.optJSONArray("web_balls")?.let { a -> (0 until a.length()).map { a.getString(it) } }
        ?: listOf("Quick Ball", "Timer Ball", "Repeat Ball")

    init {
        for (rule in targets + notify + post) {
            val bad = rule.keys - Rules.FIELDS.toSet() - "not_species"
            require(bad.isEmpty()) {
                "unknown field(s) ${bad.sorted().joinToString(", ")} in ${Rules.show(rule)}; " +
                    "use ${Rules.FIELDS.joinToString(", ")}"
            }
        }
        require(targets.isNotEmpty()) { "no targets, nothing to hunt for" }
    }

    fun str(key: String) = catch[key]?.toString() ?: ""
    fun num(key: String) = (catch[key] as? Number)?.toDouble() ?: 0.0

    @Suppress("UNCHECKED_CAST")
    private fun rules(key: String): List<Map<String, Any>> {
        val a = o.optJSONArray(key) ?: return emptyList()
        return (0 until a.length()).map { plain(a.getJSONObject(it)) as Map<String, Any> }
    }

    companion object {
        /** From secrets.json at build time (app/build.gradle.kts): not in the repository. */
        val DEBUG_WEBHOOK: String = BuildConfig.DEBUG_WEBHOOK

        val CATCH_PLAN: Map<String, Any> = mapOf(  // config "catch" overrides these
            "first_ball" to "Quick Ball", "sleep_move" to "Yawn", "guard_move" to "Protect",
            "asleep_ball" to "Repeat Ball", "awake_ball" to "Repeat Ball", "max_balls" to 8,
            "max_turns" to 30, "legendary_max_balls" to 30, "legendary_max_turns" to 90,
            "heal_below" to 0.4, "heal_item" to "Full Restore",
            "pp_item" to "Max Elixir", "on_fail" to "hold",
            "sleep_with" to "Clodsire")  // the lead that knows Yawn and Protect; others just throw balls

        /** JSON as Kotlin maps, lists, strings, numbers and booleans. */
        fun plain(v: Any?): Any? = when (v) {
            is JSONObject -> v.keys().asSequence().associateWith { plain(v.get(it)) }
            is JSONArray -> (0 until v.length()).map { plain(v.get(it)) }
            JSONObject.NULL -> null
            else -> v
        }
    }
}

// ---------------------------------------------------------------- reading the enemy

object Reader {
    val STATUSES = listOf("SLP", "PSN", "PAR", "BRN", "FRZ", "TOX")

    fun cleanName(text: String): String {
        val t = text.split(Regex("\\s+l[vy]\\b", RegexOption.IGNORE_CASE))[0]  // "Marill Lv. 52"
        val words = t.replace(Regex("[^A-Za-z0-9 .'\\-:]"), " ").split(Regex("\\s+"))
            .filter { it.isNotEmpty() }.toMutableList()
        while (words.size > 1 && words.last().length == 1) words.removeAt(words.size - 1)  // misread gender sign
        return words.joinToString(" ").trim(' ', '.')
    }

    /** The enemy from the text read in its box (Ref.BOX) and its icons. */
    fun enemy(texts: List<TextHit>, icons: Pair<Boolean, Boolean>): Enc {
        val hp = (Ref.HP_BAR_END - Ref.BOX.t).toDouble() / Ref.BOX.h
        val end = (Ref.BOX_END - Ref.BOX.t).toDouble() / Ref.BOX.h
        var species = ""; var level = ""; var reskin = ""; var gradient = ""; var status = ""
        for (hit in texts.sortedBy { it.x }) {
            var text = hit.text
            val words = text.split(Regex("\\s+")).filter { it.isNotEmpty() }
            val badge = words.map { it.uppercase() }.filter { it in STATUSES }
            if (badge.isNotEmpty()) {  // the SLP / PSN ... badge left of the HP bar
                status = badge[0]
                text = words.filter { it.uppercase() !in STATUSES }.joinToString(" ")
                if (text.isEmpty()) continue
            }
            val lv = Regex("^\\s*l[vy][.,:]?\\s*(\\d+)", RegexOption.IGNORE_CASE).find(text)
            when {
                lv != null -> level = lv.groupValues[1]
                hit.y < hp && hit.x < 0.6 -> species = "$species ${cleanName(text)}".trim()
                !Regex("[A-Za-z]{2}").containsMatchIn(text) -> {}  // an icon read as text ("*" for the shiny star)
                hit.y >= hp && hit.y < end -> reskin = "$reskin ${text.trim()}".trim()
                hit.y >= end -> gradient = "$gradient ${text.trim()}".trim()
            }
        }
        // the gender sign can come as a text of its own ("Sentret" "O"): cleaned once more, joined
        return Enc(cleanName(species), level, reskin, gradient, status, icons.first, icons.second)
    }

    val TABS = listOf("Recovers", "Pokeballs", "Key Items", "Evolution", "Competitive", "TMs",
                              "Held Items", "Z-Crystals")

    /** Where the backpack's tab is. On a phone the labels are tiny and some
     *  get misread ("TMB", "Heid Iterme"), but the tabs sit in a fixed order,
     *  evenly spaced: one that isn't read is worked out from the ones that are. */
    fun findTab(texts: List<TextHit>, want: String, split: Double, width: Int): TextHit? {
        val i = TABS.indexOfFirst { it.equals(want, true) }
        if (i < 0) return texts.firstOrNull { it.text.equals(want, true) }
        val hits = texts.filter { it.x < split }.mapNotNull { t ->
            TABS.indexOfFirst { Rules.norm(it) == Rules.norm(t.text) || Rules.sameWord(it, t.text) }
                .takeIf { it >= 0 }?.let { it to t }
        }
        if (hits.isEmpty()) return null
        val col = hits.minOf { it.second.x }  // the tab column; the list's heading is further right
        val inCol = hits.filter { abs(it.second.x - col) < 0.04 * width }
        inCol.firstOrNull { it.first == i }?.let { return it.second }
        if (inCol.map { it.first }.toSet().size < 2) return null
        val n = inCol.map { it.first.toDouble() }
        val y = inCol.map { it.second.y }
        val mn = n.average()
        val my = y.average()
        val step = n.indices.sumOf { (n[it] - mn) * (y[it] - my) } / n.indices.sumOf { (n[it] - mn) * (n[it] - mn) }
        return TextHit(want, inCol.map { it.second.x }.sorted()[inCol.size / 2], my + step * (i - mn))
    }

    /** "PP / max" under a move. The phone reads the slash as | or I now and then,
     *  or loses it ("40 40"); nothing else there has numbers. */
    val PP = Regex("(\\d+)\\s*(?:[/|Il\\\\]|\\s)\\s*(\\d+)")

    /** Your Pokemon from the text read in its box (Ref.PLAYER_BOX). */
    fun lead(texts: List<TextHit>): Lead {
        val sorted = texts.sortedWith(compareBy({ it.y }, { it.x }))
        val all = sorted.joinToString(" ") { it.text }  // "376 / 376" can come in pieces
        val hp = Regex("(\\d+)\\s*[/|]\\s*(\\d+)").find(all)  // the slash is read as | now and then
        val level = Regex("\\bl[vy][.,:]?\\s*(\\d+)", RegexOption.IGNORE_CASE).find(all)
        val name = sorted.map { cleanName(it.text) }.firstOrNull {
            Regex("[A-Za-z]{2}").containsMatchIn(it) && !it.equals("exp", true) &&
                !Regex("^l[vy]\\b", RegexOption.IGNORE_CASE).containsMatchIn(it)
        } ?: ""
        return Lead(name, level?.groupValues?.get(1)?.toInt(), hp?.groupValues?.get(1)?.toInt(),
                    hp?.groupValues?.get(2)?.toInt())
    }

    /** The moves in the Fight menu, from the text read in Ref.MSG: up to four
     *  buttons side by side, the name on top and "PP / max" under it. */
    fun moves(texts: List<TextHit>): List<Move> {
        val pp = Reader.PP
        val pps = texts.filter { it.y >= 0.5 }.mapNotNull { t -> pp.find(t.text)?.let { (t.x * 4).toInt() to it } }
        // left to right; one hit can hold two buttons' PP ("30 / 35 Normal Physical 40 / 40")
        val inOrder = texts.filter { it.y >= 0.5 }.sortedBy { it.x }.flatMap { t -> pp.findAll(t.text).toList() }
        val names = texts.filter {
            it.y < 0.5 && Regex("[A-Za-z]{2}").containsMatchIn(it.text) && !Regex("[\\d?!]").containsMatchIn(it.text) &&
                it.text.trim().length <= 22 && !it.text.trim().equals("back", true)
        }.sortedBy { it.x }
        return names.mapIndexed { i, t ->
            // one PP per button: in order; else by the button the text sits in
            val m = if (inOrder.size == names.size) inOrder[i] else pps.firstOrNull { it.first == (t.x * 4).toInt() }?.second
            Move(t.text.trim(), m?.groupValues?.get(1)?.toInt(), m?.groupValues?.get(2)?.toInt())
        }
    }

    /** Your Pokemon's HP as a fraction, from the text in its box. */
    fun hp(text: String): Double? {
        val m = Regex("(\\d+)\\s*/\\s*(\\d+)").find(text) ?: return null
        val max = m.groupValues[2].toInt()
        return if (max > 0) m.groupValues[1].toInt().toDouble() / max else null
    }
}
