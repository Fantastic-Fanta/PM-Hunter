# PM-Hunter

Nothing to configure: paste your Discord webhook into the pill's box (or the
phone app's), and that's all. The Android build reports to its debug channel from `secrets.json`, which isn't
in the repository: `{"android_debug_webhook": "..."}` (Windows' is a GitHub secret).

Hunts wild encounters in the Roblox Pokemon game: runs from the ones you don't
want, catches the ones worth a ball, and pings Discord and holds the battle for
you when a wanted one shows up: any Pokemon with a reskin and a gradient.

The layout is fitted to each screen on its first encounter (auto-calibration),
so any window size or phone shape works. It's refitted whenever a battle can't
be read.

## How it's built

All of it is Rust, one Cargo workspace:

- `core/` (hunt-core): the hunter itself, the same on every platform: reading
  the screen by its colors (the Mute button, the battle menu, the enemy's box,
  the bag), fitting the layout, the rules, the catch plan, the bag and MENU,
  saving, the website link, the Discord and debug channel posts. What it needs
  from a computer or phone (screenshots, clicks or taps, text recognition) is a
  `Device`. `cargo test -p hunt-core` checks it against `samples/`
  (`core/tests/parity.json`: what the old Python hunter saw on each, to the
  pixel), squeezed into phone shapes too, and runs a hunt on a pretend screen.
- `window/` (Hunt, Hunt.exe): the pill with the hunter in it, and each
  system's eyes and hands (`window/src/desktop/`): on a Mac, Quartz screenshots
  and mouse, Vision's text recognition; on Windows, GDI screenshots, SendInput,
  Windows.Media.Ocr. `Hunt --terminal` is the hunter alone in a Terminal
  (`--watch`, `--paused`, `--debug`, `--practice N`, `--calibrate`);
  `Hunt --diagnose` reads the samples, looks at the screen, moves the mouse;
  `Hunt --image x.png` reads a screenshot; `Hunt --test-discord`.
- `android/rust/` (libhunt.so): hunt-core for the Android app, reached from
  Kotlin through UniFFI; `android/` is the app around it (the accessibility
  service's screenshots and taps, ML Kit's text recognition, the screens).

`cargo run --release -p hunt-core --example bench` times the hunter's own work.

## Mac

**Hunt.app**: `./make-mac-app.sh` builds it in this folder (Clodsire's icon,
signed with this Mac's Apple Development certificate so macOS keeps its Screen
Recording, Accessibility and Input Monitoring permissions; what it saves stays
in this folder). It updates itself from https://poopihunt.xyz/mac.json when it
opens and whenever a hunt stops. `./publish-mac.sh` publishes a build for any
Mac (Apple silicon and Intel): `Hunt-mac.zip`, `mac.json` and the download
page, which shows each browser its own system's download. On another Mac it
keeps what it saves in ~/Library/Application Support/Hunt; the first time,
macOS says it can't check the app: System Settings > Privacy & Security >
**Open Anyway**.

Double-click `Poopihunt.command` for Poopihunt's window (the pill, as on Windows; it
builds Hunt the first time, which needs Rust: `brew install rust`), or
`pokehunt.command` for the hunter in Terminal alone. Ctrl+Shift+H pauses and
resumes. Terminal holds the Screen Recording and Accessibility grants, so
Hunt runs from it. The hunter's screenshots are of what's under the pill.
There's no self-update (a `git pull` brings the new version; Poopihunt.command
builds Hunt again).

Every reskin with a gradient is caught, on every platform. That and the catch
plan are built in: config.json keeps only the webhook (anything else in an old
one is passed over).

## Windows

**Poopihunt for Windows** is `PoopihuntSetup.exe` on https://poopihunt.xyz: download,
open it, and Poopihunt is in the Start menu and on the desktop (no admin, no
Python). Windows warns about it at first ("Windows protected your PC": **More
info > Run anyway**) because the installer isn't signed.

The window (Hunt.exe, Rust: `window/`, egui) is one pill with no frame, on top
of every window but out of screenshots (the hunter's look through it), dragged
anywhere: start / stop (play again after a find or Ctrl+Shift+H carries on; a
right click is **Debug mode**: it hunts at once, and at the next encounter fits
the layout, throws a Quick Ball (Yawn with the Yawn user), runs, uses the
favorited heal and PP items from MENU > Bag, saves, each step to the debug
channel; then it turns itself off and hunts on, or pauses if a step went
wrong; a new install's first hunt starts with one by itself), the website code (there from the start: the hunter runs paused
from when Hunt opens; click it for the website), a box for the Discord
webhook (saved in config.json; a hunt that's on starts again with it), Hunt's
folder and quit. It opens with Clodsire (`windows/hunt-icon.png`) big in the
middle of the dimmed screen, flying into the pill's end as play; it closes
by shrinking right into a ball. A find turns it white. The hunter runs inside
Hunt.exe (paused from when it opens), on a thread of its own. It
catches the optimal way (a Quick Ball first, Repeat Balls, Timer Balls once 9
turns have gone by; a favorited Park Ball before all of them). What a PC keeps
(config.json with the webhook, finds,
calibration, the website key, window.json for the pill's place) is in
`%LOCALAPPDATA%\Hunt`, with hunt.log: all the hunter printed (hunt.old.log
past 5 MB). Hunt
checks https://poopihunt.xyz/windows.json when it opens and whenever a hunt
stops; a newer build is downloaded, installed, and Hunt starts again.

`./publish-windows.sh` builds it on GitHub (`.github/workflows/windows.yml`:
the tests, `cargo build --release -p hunt`, Inno Setup with `windows/hunt.iss`)
and publishes it; GitHub builds what's pushed, so push first. The build also
reads the samples with Windows' text recognition (`Hunt.exe --diagnose`, the
"read like on the Mac" line), and Hunt.exe has to come up and go through its
states (`--smoke`, with a picture of the screen in the run's `pill` files).
Pushes build it too, as a check. On a Mac the same window:

    HUNT_DATA=$PWD cargo run --release -p hunt

(`HUNT_WATCH=1` too, for one that never clicks; `--start` hunts at once,
`HUNT_PREVIEW=1` never does; `--smoke --intro` goes through its states with
the opening and the closing.) The icons, every one from `windows/hunt-icon.png`
(Windows', the window's, Android's, the Mac's): `python windows/make_icon.py`.

Published builds report debug runs in full (their lines, calibration, the
first 10 clicks, each step's picture) and otherwise only what goes wrong (the
line with the 15 before it, its screenshot, crashes) to the Windows debug
channel (`core/src/out.rs`; the address is the repository's
`WINDOWS_DEBUG_WEBHOOK` secret, built in, `"debug_webhook"` in config.json changes it,
`""` turns it off, so does **Send problem reports**).

Clicks go through Windows' `SendInput` with small relative mouse moves, the way
a real mouse reaches Roblox. Text is read with Windows' own OCR. Keep Roblox on
the main screen.

## Android (just the phone, no computer)

The app is in `android/`; the download is on https://poopihunt.xyz.

1. Install it on the phone (allow installing from the browser when Android asks).
2. Open Poopihunt: a pill shows for each thing still to allow. **Accessibility ·
   Turn on**: switch Poopihunt on (greyed out? Settings > Apps > Poopihunt, ⋮
   top right > **Allow restricted settings**, and again). Some phones
   (emulators especially) give blank screenshots: then **Screen capture ·
   Allow** shows too. Each pill goes once it's on.
3. Paste your Discord webhook into **Paste Discord webhook here**.
4. Open Roblox. The pill floats over it, right after Roblox's menu and chat
   buttons: play starts hunting (it turns to stop), a long press is a debug
   run (the bug), the website's code beside it copies when tapped (a long press
   makes a new one). Drag it anywhere; it stays there. The Quick Settings tile
   is the same play / stop.

When it finds one the pill turns white, the phone vibrates and rings, and it
keeps the battle alive until you touch the screen.

Needs Android 11 or newer. To find a ball, Full Restore or Max Elixir it types
its name into the bag's search box (on Android 13+ through its own input
method; Roblox draws its text boxes itself) and scrolls through that tab of the
bag if typing doesn't work. Problems go to the debug channel.

## Website: watch and battle from anywhere

https://poopihunt.xyz/app. Hunt gets a 6-character code each time it starts:
the phone's pill shows it (a long press for a new one; tapping the "a browser
logged in" notification logs every browser out), the Mac/Windows pill shows it (Ctrl+Shift+N for a new
one, Ctrl+Shift+L logs every browser out; it never goes to the debug
channel). Type the code on the site and that browser is logged in for 30
days. A code works once; the phone (or window) says when a browser logs in.
Each browser sees only the devices it logged in with; **Add a device** logs
in another one.

**Take over** pauses the bot in whatever battle is on (or the next one): the
page shows the encounter, a picture after every turn, your lead's HP, and
buttons to throw a ball (Quick, Timer and Repeat), use a move, Restore HP / PP (the catch plan's heal and PP items) or
Run. **Resume hunting** hands it back. Started in the middle of a battle,
Hunt leaves that battle to you and switches itself off. Watch only doesn't
connect (it never clicks).

The site is `server/hunt_server.py` (the API, as the `poopihunt` user, behind
nginx's poopihunt.xyz site) and `site/app/index.html` (the page):
`./publish-server.sh` updates both (`--setup` the first time).
`server/test_server.py` checks its rules; `server/fake_device.py` is a
pretend phone for trying the page.

Updates: `./publish-android.sh` builds the app and publishes it on
https://poopihunt.xyz (a download page, the APK and `version.json`;
hunt.stingray-digital.online serves the same, for older builds). A phone
updates itself when you press stop on the pill, never mid-hunt (Android asks
once to allow it); the debug channel hears "updated: old → new".
poopihunt.xyz lives on the stingray server in its own folder
(`/srv/poopihunt.xyz`, owner `poopihunt`, nginx site `poopihunt.xyz`, the API
as the `poopihunt-api` service), apart from stingray-digital.online.

Building it: `cd android && ./gradlew assembleDebug` (needs the Android SDK and
NDK 27, rustup's Rust with `rustup target add aarch64-linux-android`, and
`cargo install cargo-ndk`: Gradle builds libhunt.so and its Kotlin first; the
APK lands in `app/build/outputs/apk/debug/`). `./gradlew testDebugUnitTest`
runs the Rust hunter through the app's Kotlin on this computer.
