package com.pokehunt

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.view.WindowManager
import java.nio.ByteOrder

/** Screenshots through Android's screen recording, for when the
 *  accessibility service's screenshots come back blank (some emulators).
 *  Android asks once per hunt ("Start recording or casting?"). */
class CaptureService : Service() {
    companion object {
        const val MAX_WIDTH = 2000
        @Volatile var instance: CaptureService? = null
            private set
    }

    private var projection: MediaProjection? = null
    private var display: VirtualDisplay? = null
    private var reader: ImageReader? = null
    private val thread = HandlerThread("capture").apply { start() }
    private val handler = Handler(thread.looper)
    @Volatile var shrink = 1.0  // screen px per frame px
        private set
    private var last: Frame? = null  // a still screen sends no new pictures

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val channel = NotificationChannel("capture", "Screen capture", NotificationManager.IMPORTANCE_LOW)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        val note = Notification.Builder(this, "capture")
            .setSmallIcon(android.R.drawable.star_big_on)
            .setContentTitle("Hunt is watching the screen")
            .build()
        startForeground(2, note, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        val code = intent?.getIntExtra("code", 0) ?: 0
        @Suppress("DEPRECATION")  // the typed version is Android 13+
        val data: Intent? = intent?.getParcelableExtra("data")
        if (data == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        val p = getSystemService(MediaProjectionManager::class.java).getMediaProjection(code, data)
        p.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() = stopSelf()
        }, handler)
        projection = p
        fit()
        instance = this
        Log.print("  watching the screen through screen capture")
        return START_NOT_STICKY
    }

    /** The virtual display matches the screen's current shape (it turns
     *  with the phone), at most MAX_WIDTH wide. */
    private fun fit() {
        val p = projection ?: return
        val b = getSystemService(WindowManager::class.java).maximumWindowMetrics.bounds
        val k = minOf(1.0, MAX_WIDTH.toDouble() / b.width())
        val w = (b.width() * k).toInt()
        val h = (b.height() * k).toInt()
        val r = reader
        if (r != null && r.width == w && r.height == h) return
        val nr = ImageReader.newInstance(w, h, PixelFormat.RGBA_8888, 2)
        val dpi = resources.displayMetrics.densityDpi
        val d = display
        if (d == null) {
            display = p.createVirtualDisplay("pokehunt", w, h, dpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, nr.surface, null, handler)
        } else {
            d.resize(w, h, dpi)
            d.surface = nr.surface
        }
        r?.close()
        reader = nr
        shrink = 1 / k
    }

    /** The newest picture of the screen, or null if there's none yet. */
    @Synchronized
    fun grab(): Frame? {
        fit()
        val img = reader?.acquireLatestImage() ?: return last?.takeIf { it.width == reader?.width }
        img.use {
            val plane = it.planes[0]
            val ints = plane.buffer.order(ByteOrder.LITTLE_ENDIAN).asIntBuffer()
            val w = it.width
            val h = it.height
            val px = IntArray(w * h)
            for (y in 0 until h) {
                ints.position(y * plane.rowStride / 4)
                ints.get(px, y * w, w)
            }
            // RGBA bytes read as little-endian ints are ABGR: swap R and B
            for (i in px.indices) {
                val c = px[i]
                px[i] = (c and 0x0000FF00) or ((c and 0xFF) shl 16) or ((c shr 16) and 0xFF) or (0xFF shl 24)
            }
            return Frame(w, h, px).also { f -> last = f }
        }
    }

    override fun onDestroy() {
        instance = null
        display?.release()
        reader?.close()
        projection?.stop()
        thread.quitSafely()
        super.onDestroy()
    }
}
