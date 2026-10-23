# PM-Hunter

Copy `config.example.json` to `config.json` and put your Discord webhook in it.
Builds that report to the debug channels (`make-windows-zip.sh`,
`publish-android.sh`) read the addresses from `secrets.json`, which isn't in
the repository: `{"windows_debug_webhook": "...", "android_debug_webhook": "..."}`.

Hunts wild encounters in the Roblox Pokemon game: runs from the ones you don't
want, catches the ones worth a ball, and pings Discord and holds the battle for
you when a wanted one shows up. What's wanted is in `config.json`.

The layout is fitted to each screen on its first encounter (auto-calibration),
so any window size or phone shape works. It's refitted whenever a battle can't
be read.

## Mac

Double-click `pokehunt.command`. Ctrl+Shift+H pauses and resumes.

Every reskin with a gradient is caught, on every platform, without a rule
for it: `"catch_reskin_gradient": false` in config.json turns that off.

## Windows

**Hunt for Windows** is `HuntSetup.exe` on https://poopihunt.xyz: download,
open it, and Hunt is in the Start menu and on the desktop (no admin, no
Python). Windows warns about it at first ("Windows protected your PC": **More
info > Run anyway**) because the installer isn't signed.

The window (`app.py`, Hunt.exe) is a pill with no frame, dragged anywhere:
start / stop (play again after a find or Ctrl+Shift+H carries on), the website
code (click it for the website), a box for the Discord webhook (saved in
config.json; a hunt that's on starts again with it; right click pastes), and
**•••** for the rest: **Watch only**, **Finds**, Check this PC, Test catching, fit the layout
again, **Send problem reports** and Close. A find turns it white. The
hunter itself is pokehunt.py
(hunt-engine.exe), run by the window with `--window`. What a PC keeps
(config.json, finds, calibration, the website key) is in `%LOCALAPPDATA%\Hunt`
(**••• > Open Hunt's folder**), with hunt.log: all the hunter printed (hunt.old.log
past 5 MB). The window has no settings or log of its own: a
new config.json has no rules, so every reskin with a gradient is caught, and
anything more (targets, a webhook, the catch plan) is edited in that file.
Hunt checks https://poopihunt.xyz/windows.json when it starts (and every 6
hours); a newer build puts **Update Hunt** at the top of **•••**, which
installs it and restarts.

`./publish-windows.sh` builds it on GitHub (`.github/workflows/windows.yml`:
PyInstaller with `windows/hunt.spec`, Inno Setup with `windows/hunt.iss`,
Python 3.12) and publishes it; GitHub builds what's pushed, so push first.
The build also reads the samples with Windows' OCR (the "read like on the
Mac" line). Pushes build it too, as a check. On a Mac, `python app.py` opens
the same pill (with dark corners there: Tk on a Mac won't make them clear).

Published builds report what they print, their errors, and screenshots of
calibration, trouble, finds and the first 10 clicks to the Windows debug
channel (`debuglog.py`; the address is the repository's
`WINDOWS_DEBUG_WEBHOOK` secret, `"debug_webhook"` in config.json changes it,
`""` turns it off, so does **Send problem reports**).

Clicks go through Windows' `SendInput` with small relative mouse moves, the way
a real mouse reaches Roblox. Text is read with Windows' own OCR. Keep Roblox on
the main screen.

The older test zip (`./make-windows-zip.sh`, needs Python on the PC, then
**1 Check**, **2 Watch only**, **3 Hunt**) still works.

## Android (just the phone, no computer)

The app is in `android/`. `Hunt.apk` is the built copy.

1. Put `Hunt.apk` on the phone (Google Drive, a USB cable, Discord, ...) and
   open it. Allow installing from that app when Android asks.
2. Open Hunt and tap **Turn on Hunt**, then switch Hunt on. If it's
   greyed out, go to Settings > Apps > Hunt, tap ⋮ (top right) > **Allow
   restricted settings**, and try again.
3. Open **What to hunt**, paste your `config.json` (with your webhook) and tap
   **Save**.
4. Open Roblox. A small **OFF** button floats at the right whenever Roblox is
   on screen: tap it to start hunting (it turns **ON**), tap again to stop. Drag
   it out of the way if it covers something. **Start** in the app does the same.
   Some Androids (emulators especially) give apps blank screenshots; then
   Android asks "Start recording or casting with Hunt?": tap **Start now**,
   it's how Hunt sees the game there.

When it finds one, the phone vibrates and rings. It keeps the battle alive
until you touch the screen.

Needs Android 11 or newer. To find a ball, Full Restore or Max Elixir it types
its name into the bag's search box (keeping the keyboard hidden meanwhile),
and scrolls through that tab of the bag if typing doesn't work.

Under **More**: **Calibrate** draws the fitted layout on a
picture in Pictures/Hunt, and **Share hunt log** sends what it did. Send
me those and any `trouble` pictures if something goes wrong.

## Website: watch and battle from anywhere

https://poopihunt.xyz/app. Hunt gets a 6-character code each time it starts:
the Android app shows it under the status (with **New code** and **Log out
browsers** buttons), the Mac/Windows window prints it (Ctrl+Shift+N for a new
one, Ctrl+Shift+L logs every browser out; it never goes to the debug
channel). Type the code on the site and that browser is logged in for 30
days. A code works once; the phone (or window) says when a browser logs in.
Each browser sees only the devices it logged in with; **Add a device** logs
in another one.

**Take over** pauses the bot in whatever battle is on (or the next one): the
page shows the encounter, a picture after every turn, your lead's HP, and
buttons to throw a ball (`"web_balls"` in config.json, Timer and Repeat by
default), use a move, Restore HP / PP (the catch plan's heal and PP items) or
Run. **Resume hunting** hands it back. Started in the middle of a battle,
Hunt leaves that battle to you and switches itself off. Watch only doesn't
connect (it never clicks).

The site is `server/hunt_server.py` (the API, as the `poopihunt` user, behind
nginx's poopihunt.xyz site) and `site/app/index.html` (the page):
`./publish-server.sh` updates both (`--setup` the first time).
`server/test_server.py` checks its rules; `server/fake_device.py` is a
pretend phone for trying the page.

Updates: `./publish-android.sh` builds the app and publishes it on
https://poopihunt.xyz (a download page, `Hunt.apk` and `version.json`;
hunt.stingray-digital.online serves the same, for older builds). Every
phone shows **Update** the next time Hunt opens (Android asks once to allow
installs from Hunt). The bottom of the app says which build a phone has.
poopihunt.xyz lives on the stingray server in its own folder
(`/srv/poopihunt.xyz`, owner `poopihunt`, nginx site `poopihunt.xyz`, the API
as the `poopihunt-api` service), apart from stingray-digital.online.

Building it: `cd android && ./gradlew assembleDebug` (needs the Android SDK; the
APK lands in `app/build/outputs/apk/debug/`). `./gradlew testDebugUnitTest`
checks the screen reading against `samples/`.
