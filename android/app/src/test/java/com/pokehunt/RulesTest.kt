package com.pokehunt

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class RulesTest {
    private val cfg = Config(File("../../config.json").readText())

    @Test fun ratioMatchesDifflib() {
        // from Python's difflib.SequenceMatcher(None, a, b).ratio()
        assertEquals(0.8333, Rules.ratio("lapras", "lapros"), 1e-4)
        assertEquals(0.9231, Rules.ratio("suicune", "suicun"), 1e-4)
        assertEquals(0.8571, Rules.ratio("glacier", "glaciar"), 1e-4)
        assertEquals(0.9474, Rules.ratio("pinstripe", "pinstripes"), 1e-4)
        assertEquals(0.875, Rules.ratio("moonbane", "moonbone"), 1e-4)
        assertEquals(0.9231, Rules.ratio("azurine", "zurine"), 1e-4)
    }

    @Test fun matchesTheConfig() {
        assertNotNull(Rules.wanted(Enc("Lapras", reskin = "DOODLE", gradient = "Mirage"), cfg.targets))
        assertNotNull(Rules.wanted(Enc("Suicune", reskin = "Doodle"), cfg.targets))
        assertNull(Rules.wanted(Enc("Lapras", reskin = "Doodle"), cfg.targets))
        assertNull(Rules.wanted(Enc("Marill", reskin = "INFERNO", tint = true), cfg.targets))
        // auto-catch: legendary with a reskin, any reskin + gradient, shiny Doodle but not Frillish
        assertNotNull(Rules.wanted(Enc("Galarian Articuno", reskin = "Night"), cfg.notify))
        assertNotNull(Rules.wanted(Enc("Marill", reskin = "INFERNO", gradient = "Mirage"), cfg.notify))
        assertNotNull(Rules.wanted(Enc("Marill", reskin = "Doodle", shiny = true), cfg.notify))
        assertNull(Rules.wanted(Enc("Frillish", reskin = "Doodle", shiny = true), cfg.notify))
        assertNull(Rules.wanted(Enc("Marill", reskin = "INFERNO"), cfg.notify))
    }

    @Test fun readsTheEnemyBox() {
        // as the text recognition reports marill-inferno-tint.png's box
        val texts = listOf(TextHit("Marill", 0.17, 0.2), TextHit("Lv. 52", 0.8, 0.2),
                           TextHit("INFERNO", 0.67, 0.65))
        val enc = Reader.enemy(texts, false to true)
        assertEquals(Enc("Marill", "52", "INFERNO", tint = true), enc)
        assertEquals("Marill Lv52  (reskin INFERNO, tint)", enc.describe())
        assertEquals("Dewpider", Reader.cleanName("Dewpider o"))
        assertEquals("SLP", Reader.enemy(listOf(TextHit("SLP", 0.1, 0.45)), false to false).status)
        assertEquals("Mirage", Reader.enemy(listOf(TextHit("Mirage", 0.3, 0.9)), false to false).gradient)
        // a phone reads the gender sign as a text of its own
        assertEquals("Sentret", Reader.enemy(listOf(TextHit("Sentret", 0.1, 0.2), TextHit("O", 0.4, 0.2),
                                                     TextHit("Lv. 3", 0.8, 0.2)), false to false).species)
    }

    @Test fun findsBagTabsFromTheOnesRead() {
        // what the text recognition read on the Android 16 emulator's backpack:
        // "Recovers" and "Evolution" missed, "TMs" and "Held Items" garbled
        val texts = listOf(TextHit("Backpack", 445.0, 204.0), TextHit("Pokeballs", 430.0, 322.0),
            TextHit("Key Items", 429.0, 366.0), TextHit("Competitive", 429.0, 451.0), TextHit("TMB", 429.0, 493.0),
            TextHit("Heid Iterme", 429.0, 537.0), TextHit("Z-Crystals", 429.0, 580.0),
            TextHit("Recover", 553.0, 263.0), TextHit("Antidote", 616.0, 324.0), TextHit("Elixir", 602.0, 477.0))
        val split = 1001.0
        val recovers = Reader.findTab(texts, "Recovers", split, 1676)!!
        assertEquals(429.0, recovers.x, 2.0)
        assertEquals(280.0, recovers.y, 6.0)  // the Recovers tab, not the "Recover" heading
        assertEquals(322.0, Reader.findTab(texts, "Pokeballs", split, 1676)!!.y, 0.1)
        assertEquals(409.0, Reader.findTab(texts, "Evolution", split, 1676)!!.y, 6.0)
    }

    @Test fun sameMonForgivesMisreads() {
        assertTrue(Rules.sameMon(Enc("Type: Null", "60"), Enc("Type: Nulll", "60")))
        assertFalse(Rules.sameMon(Enc("Lapras", "60"), Enc("Lapras", "61")))
    }

    // the text read in samples/fight-menu.png's boxes, as ML Kit splits it

    @Test fun readsTheLead() {
        val box = listOf(TextHit("Clodsire ♀ Lv. 109", 0.5, 0.27), TextHit("376 / 376", 0.8, 0.81), TextHit("Exp", 0.12, 0.9))
        assertEquals(Lead("Clodsire", 109, 376, 376), Reader.lead(box))
        // in pieces, the level first
        val pieces = listOf(TextHit("Lv. 109", 0.86, 0.27), TextHit("Clodsire", 0.25, 0.27), TextHit("350", 0.7, 0.81),
                            TextHit("/ 376", 0.85, 0.81))
        assertEquals(Lead("Clodsire", 109, 350, 376), Reader.lead(pieces))
        assertEquals(Lead(), Reader.lead(emptyList()))
        assertTrue(Reader.lead(box).complete)
        // ML Kit reads the dot as a comma now and then
        assertEquals(Lead("Sobble", 11, 29, 29), Reader.lead(listOf(TextHit("Sobble", 0.2, 0.27), TextHit("Exp", 0.1, 0.9),
                                                                    TextHit("Lv, 11", 0.85, 0.3), TextHit("29 / 29", 0.8, 0.8))))
        // the level-up stats panel over the box: not a reading to check an answer against
        val panel = listOf(TextHit("Sobble Sp.A:", 0.3, 0.27), TextHit("Exp", 0.12, 0.9), TextHit("Sp.D:", 0.3, 0.5),
                           TextHit("Speed:", 0.3, 0.7), TextHit("16 ta", 0.8, 0.3))
        assertFalse(Reader.lead(panel).complete)
    }

    @Test fun readsTheMoves() {
        val menu = listOf(TextHit("Yawn", 0.125, 0.26), TextHit("Protect", 0.375, 0.26),
                          TextHit("10 / 10", 0.047, 0.84), TextHit("Normal", 0.125, 0.86), TextHit("Status", 0.2, 0.84),
                          TextHit("7 / 10 Normal Status", 0.32, 0.84))
        assertEquals(listOf(Move("Yawn", 10, 10), Move("Protect", 7, 10)), Reader.moves(menu))
        // four moves, two buttons' PP read as one line
        val four = listOf(TextHit("Pound", 0.125, 0.26), TextHit("Growl", 0.375, 0.26), TextHit("Water Gun", 0.625, 0.26),
                          TextHit("Bind", 0.875, 0.26), TextHit("30 / 35 Normal Physical 40 / 40 Normal", 0.26, 0.84),
                          TextHit("25 / 25", 0.55, 0.84), TextHit("Water Special", 0.65, 0.84), TextHit("20 / 20", 0.8, 0.84))
        assertEquals(listOf(Move("Pound", 30, 35), Move("Growl", 40, 40), Move("Water Gun", 25, 25), Move("Bind", 20, 20)),
                     Reader.moves(four))
        // read on MuMu: one slash lost, one read as |
        val mumu = listOf(TextHit("Pound", 0.125, 0.26), TextHit("Growl", 0.375, 0.26), TextHit("Water Gun", 0.625, 0.26),
                          TextHit("Bind", 0.875, 0.26), TextHit("30/35 Normal Physical 40 40 Normal Status", 0.25, 0.84),
                          TextHit("19|25 Water Special 20/ 20 Normal Physical", 0.75, 0.84))
        assertEquals(listOf(Move("Pound", 30, 35), Move("Growl", 40, 40), Move("Water Gun", 19, 25), Move("Bind", 20, 20)),
                     Reader.moves(mumu))
        // the battle's text before the menu is up is no move
        assertEquals(emptyList<Move>(), Reader.moves(listOf(TextHit("What should Clodsire do?", 0.2, 0.2),
                                                             TextHit("Clodsire used Yawn!", 0.2, 0.2))))
    }
}
