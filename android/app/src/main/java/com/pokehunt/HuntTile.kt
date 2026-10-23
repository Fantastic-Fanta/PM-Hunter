package com.pokehunt

import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.Intent
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService

/** "Hunt" in Quick Settings (swipe down from the top): tap to start or
 *  stop hunting without leaving Roblox. */
class HuntTile : TileService() {
    override fun onStartListening() {
        val tile = qsTile ?: return
        tile.state = if (Hunt.running) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        tile.subtitle = when {
            HuntService.instance == null -> "Set up"
            HuntService.instance?.hunter?.running == true -> "Hunting"
            Hunt.running -> "Paused"
            else -> "Off"
        }
        tile.updateTile()
    }

    override fun onClick() {
        Log.print("  (Quick Settings tile tapped)")
        if (Hunt.running) {
            Hunt.stop()
            onStartListening()
            return
        }
        Thread {
            val problem = Hunt.start(this)
            if (problem == null) Hunt.refreshTile(this)
            else openApp(problem)  // the app sorts it out: setup, screen capture, a config mistake
        }.start()
    }

    @SuppressLint("StartActivityAndCollapseDeprecated")
    private fun openApp(problem: String) {
        val intent = Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            .putExtra("start", problem)
        if (Build.VERSION.SDK_INT >= 34)
            startActivityAndCollapse(PendingIntent.getActivity(this, 0, intent,
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
        else @Suppress("DEPRECATION") startActivityAndCollapse(intent)
    }
}
