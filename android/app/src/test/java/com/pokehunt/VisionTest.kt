package com.pokehunt

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Against the Mac screenshots in samples/: the same answers as pokehunt.py,
 *  and the same after squeezing them into phone shapes. */
class VisionTest {
    private val samples = File("../../samples")

    /** javax.imageio, reached at run time: tests compile against Android's
     *  classes, which don't have it. */
    private fun load(name: String): Frame {
        val img = Class.forName("javax.imageio.ImageIO").getMethod("read", File::class.java)
            .invoke(null, File(samples, name))
        val c = img.javaClass
        val w = c.getMethod("getWidth").invoke(img) as Int
        val h = c.getMethod("getHeight").invoke(img) as Int
        val px = c.getMethod("getRGB", Int::class.java, Int::class.java, Int::class.java, Int::class.java,
                             IntArray::class.java, Int::class.java, Int::class.java)
            .invoke(img, 0, 0, w, h, null, 0, w) as IntArray
        return Frame(w, h, px)
    }

    /** The sample on a phone screen of w x h: the game scaled sx across and
     *  sy down (Roblox fits its UI to the screen), centered on black,
     *  smoothed like a real rescale. */
    private fun phone(src: Frame, w: Int, h: Int, sx: Double, sy: Double): Frame {
        val px = IntArray(w * h) { 0xFF000000.toInt() }
        val dw = (src.width * sx).toInt()
        val dh = (src.height * sy).toInt()
        val ox = (w - dw) / 2
        val oy = (h - dh) / 2
        for (y in 0 until dh) for (x in 0 until dw) {
            val fx = (x + 0.5) / sx - 0.5
            val fy = (y + 0.5) / sy - 0.5
            val x0 = fx.toInt().coerceIn(0, src.width - 2)
            val y0 = fy.toInt().coerceIn(0, src.height - 2)
            val ax = (fx - x0).coerceIn(0.0, 1.0)
            val ay = (fy - y0).coerceIn(0.0, 1.0)
            var rgb = 0
            for (shift in intArrayOf(16, 8, 0)) {
                fun c(xx: Int, yy: Int) = (src.px[yy * src.width + xx] shr shift) and 255
                val v = c(x0, y0) * (1 - ax) * (1 - ay) + c(x0 + 1, y0) * ax * (1 - ay) +
                    c(x0, y0 + 1) * (1 - ax) * ay + c(x0 + 1, y0 + 1) * ax * ay
                rgb = rgb or (v.toInt().coerceIn(0, 255) shl shift)
            }
            val tx = ox + x
            val ty = oy + y
            if (tx in 0 until w && ty in 0 until h) px[ty * w + tx] = rgb or 0xFF000000.toInt()
        }
        return Frame(w, h, px)
    }

    private val battles = listOf("quaxly-shiny.png", "shellos-tint.png", "remoraid-shiny.png",
                                 "dewpider-mirage.png", "marill-inferno-tint.png")

    @Test fun readsTheSamplesLikePython() {
        for (name in battles) {
            val f = (load(name))
            val mute = Vision.findMute(f)
            assertEquals(name, Ref.MUTE, mute)
            val lay = Layout(mute!!, Calibration())
            assertTrue(name, Vision.menuReady(f, lay))
            assertTrue(name, Vision.boxVisible(f, lay))
            assertFalse(name, Vision.chatShowing(f, lay))
        }
        assertEquals(true to false, Vision.icons((load("quaxly-shiny.png")), Layout(Ref.MUTE, Calibration())))
        assertEquals(false to true, Vision.icons((load("shellos-tint.png")), Layout(Ref.MUTE, Calibration())))
        assertNull(Vision.findMute((load("bag-open.png"))))
        assertFalse(Vision.menuReady((load("fight-menu.png")), Layout(Ref.MUTE, Calibration())))
    }

    @Test fun calibratingTheSamplesChangesNothing() {
        for (name in battles) {
            val f = (load(name))
            val cal = Calibrate.fit(f, Vision.findMute(f)!!)
            assertNotNull(name, cal)
            assertEquals(name, 1.0, cal!!.kx, 0.01)
            assertEquals(name, 1.0, cal.ky, 0.01)
            assertTrue(name + " miss ${cal.miss}", cal.miss < 4)
        }
        assertNull(Calibrate.fit((load("fight-menu.png")), Ref.MUTE))  // no menu up
    }

    @Test fun calibratesARealPhone() {
        // the game on the phone is squeezed down more than across: unreadable until calibrated
        val f = load("phone-pikachu-shiny.png")
        val mute = Vision.findMute(f)!!
        assertFalse(Vision.menuReady(f, Layout(mute, Calibration())))
        val cal = Calibrate.fit(f, mute)!!
        assertEquals(0.93, cal.ky / cal.kx, 0.02)
        val lay = Layout(mute, cal)
        assertTrue(Vision.menuReady(f, lay))
        assertTrue(Vision.boxVisible(f, lay))
        assertEquals(true to false, Vision.icons(f, lay))
    }

    @Test fun samplesTheWildPokemonsSpriteLikePython() {
        // pixels deep inside the sprite, as pokehunt.py's send_sprite samples them
        fun check(name: String, vararg want: Triple<Int, Int, IntArray>) {
            val f = load(name)
            val mute = Vision.findMute(f)!!
            val area = Vision.spriteArea(f, Layout(mute, Calibrate.fit(f, mute)!!))
            assertEquals(96, area.width)
            for ((x, y, c) in want) assertTrue("$name $x,$y", area.near(y * 96 + x, c, 20))
        }
        check("quaxly-shiny.png", Triple(44, 30, intArrayOf(135, 240, 229)), Triple(52, 47, intArrayOf(253, 253, 251)))
        check("phone-pikachu-shiny.png", Triple(55, 33, intArrayOf(85, 85, 85)), Triple(60, 44, intArrayOf(242, 200, 80)),
              Triple(39, 47, intArrayOf(242, 169, 59)))
    }

    @Test fun roboxButtonsArentChat() {
        // Android 16 phone: Roblox's menu and chat buttons sit where chat lines start
        val f = load("phone16-pichu-shiny.png")
        val mute = Vision.findMute(f)!!
        val lay = Layout(mute, Calibrate.fit(f, mute)!!)
        assertFalse(Vision.chatShowing(f, lay))
        assertTrue(Vision.boxVisible(f, lay))
        assertEquals(true to false, Vision.icons(f, lay))
    }

    @Test fun findsRobloxsChatButton() {
        // the right end of the dark pill in the top-left corner (menu, chat)
        val a = Vision.chatButton(load("phone16-pichu-shiny.png"))!!
        assertEquals(249.0, a.x, 12.0)
        assertEquals(44.0, a.y, 8.0)
        val b = Vision.chatButton(load("phone-pikachu-shiny.png"))!!
        assertEquals(205.0, b.x, 12.0)
        assertEquals(125.0, b.y, 8.0)
    }

    @Test fun calibratesPhoneShapedScreens() {
        // (screen, how the game is squeezed across and down)
        val phones = listOf(Triple(2400 to 1080, 0.9, 0.86), Triple(2340 to 1080, 0.75, 0.86),
                            Triple(1600 to 720, 0.55, 0.57), Triple(2800 to 1260, 1.0, 1.0))
        for ((size, sx, sy) in phones) {
            for (name in battles) {
                val f = phone(load(name), size.first, size.second, sx, sy)
                val mute = Vision.findMute(f)
                assertNotNull("$name on $size", mute)
                val cal = Calibrate.fit(f, mute!!)
                assertNotNull("$name on $size squeezed $sx x $sy", cal)
                assertEquals("$name on $size", sy / sx, cal!!.ky / cal.kx, 0.03)
                val lay = Layout(mute, cal)
                assertTrue("$name on $size", Vision.menuReady(f, lay))
                assertTrue("$name on $size", Vision.boxVisible(f, lay))
                // the Run button lands on Run
                val run = lay.pt(Ref.RUN)
                val i = run.y.toInt() * f.width + run.x.toInt()
                assertTrue("$name on $size", f.near(i, Ref.BUTTONS.getValue("run").second, 30))
            }
        }
    }
}
