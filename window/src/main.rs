//! Hunt's window (Hunt.exe): a pill with no frame. Start / stop, the website
//! code, a box for the Discord webhook, and ••• which grows the pill down into
//! a panel: Debug mode, Preferred ball, and Update / Folder / Quit.
//!
//! The hunter itself is pokehunt.py (hunt-engine.exe, next to Hunt.exe), run
//! as a process of its own: what it prints goes to hunt.log, its "@state"
//! lines set the pill, and the window's buttons go to it as lines (toggle,
//! test on, test off). Closing its input stops it. What a PC keeps
//! (config.json, finds, the website key, hunt.log, window.json) is in
//! %LOCALAPPDATA%\Hunt.
//!
//! On a Mac, for trying it out:
//!   HUNT_ENGINE=".venv/bin/python -u pokehunt.py" HUNT_DATA=. cargo run --manifest-path window/Cargo.toml
#![cfg_attr(windows, windows_subsystem = "windows")]

use eframe::egui::{
    self, pos2, vec2, Align2, Color32, CursorIcon, FontFamily, FontId, Id, Pos2, Rect, Sense, Stroke, Vec2,
    ViewportCommand,
};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use std::{env, thread};

const NAME: &str = match option_env!("HUNT_NAME") {
    Some(name) => name,
    None => "dev",
};
const W: f32 = 360.0; // the pill
const H: f32 = 52.0;
const PANEL: f32 = 152.0; // what ••• adds under it
const ROW: f32 = 44.0;
const SLIDE: f32 = 0.45; // s, to grow into the panel or back

const INK: Color32 = Color32::from_rgb(0x11, 0x11, 0x11);
const PAPER: Color32 = Color32::WHITE;

fn version() -> u64 {
    option_env!("HUNT_VERSION").and_then(|v| v.parse().ok()).unwrap_or(0)
}

fn site() -> String {
    env::var("HUNT_SITE").unwrap_or_else(|_| "https://poopihunt.xyz".into())
}

/// The pill's colors: black, or white while it shows a find.
struct Look {
    fill: Color32,
    ink: Color32,
    dim: Color32,
    edge: Color32,
    hover: Color32,
    boxed: Color32,
}

fn look(light: bool) -> Look {
    if light {
        Look { fill: PAPER, ink: INK, dim: gray(0x6a), edge: gray(0xcf), hover: gray(0xe8), boxed: gray(0xef) }
    } else {
        Look { fill: INK, ink: PAPER, dim: gray(0x8a), edge: gray(0x3a), hover: gray(0x2e), boxed: gray(0x22) }
    }
}

fn gray(v: u8) -> Color32 {
    Color32::from_rgb(v, v, v)
}

// ---------------------------------------------------------------- files

struct Paths {
    data: PathBuf,
    config: PathBuf,
    settings: PathBuf,
    log: PathBuf,
}

impl Paths {
    fn new() -> Paths {
        let data = env::var_os("HUNT_DATA").map(PathBuf::from).unwrap_or_else(|| {
            if cfg!(windows) {
                PathBuf::from(env::var_os("LOCALAPPDATA").unwrap_or_default()).join("Hunt")
            } else {
                env::current_dir().unwrap_or_default()
            }
        });
        let _ = fs::create_dir_all(&data);
        Paths {
            config: data.join("config.json"),
            settings: data.join("window.json"),
            log: data.join("hunt.log"),
            data,
        }
    }
}

fn read_json(path: &Path) -> Option<Map<String, Value>> {
    match serde_json::from_str(&fs::read_to_string(path).ok()?).ok()? {
        Value::Object(map) => Some(map),
        _ => None,
    }
}

fn write_json(path: &Path, value: &Map<String, Value>) {
    let tmp = path.with_extension("json.new");
    let text = serde_json::to_string_pretty(value).unwrap_or_default() + "\n";
    if fs::write(&tmp, text).is_ok() {
        let _ = fs::rename(&tmp, path);
    }
}

fn is_webhook(url: &str) -> bool {
    ["https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/",
     "https://ptb.discord.com/api/webhooks/", "https://canary.discord.com/api/webhooks/"]
        .iter()
        .filter_map(|p| url.strip_prefix(p))
        .any(|rest| {
            let (id, token) = rest.split_once('/').unwrap_or(("", ""));
            !id.is_empty() && id.chars().all(|c| c.is_ascii_digit()) && !token.is_empty()
                && !token.chars().any(char::is_whitespace)
        })
}

/// "park" -> "Park Ball", "" -> "Repeat Ball".
fn ball_name(text: &str) -> String {
    let mut words: Vec<String> = text
        .split_whitespace()
        .map(|w| {
            let mut c = w.chars();
            c.next().map(|f| f.to_uppercase().collect::<String>() + &c.as_str().to_lowercase()).unwrap_or_default()
        })
        .collect();
    if words.is_empty() {
        return "Repeat Ball".into();
    }
    if !words.last().unwrap().ends_with("Ball") {
        words.push("Ball".into());
    }
    words.join(" ")
}

fn open_path(path: &str) {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        let wide: Vec<u16> = std::ffi::OsStr::new(path).encode_wide().chain([0]).collect();
        let verb: Vec<u16> = "open\0".encode_utf16().collect();
        unsafe {
            windows_sys::Win32::UI::Shell::ShellExecuteW(
                std::ptr::null_mut(), verb.as_ptr(), wide.as_ptr(), std::ptr::null(), std::ptr::null(), 1);
        }
    }
    #[cfg(not(windows))]
    {
        let _ = Command::new("open").arg(path).spawn();
    }
}

// ---------------------------------------------------------------- the hunter

enum Msg {
    Line(u64, String),
    Ended(u64, bool),
    Update(Map<String, Value>),
    Progress(String),
    Downloaded(PathBuf),
    UpdateFailed(String),
}

struct Proc {
    id: u64,
    child: Arc<Mutex<Child>>,
    stdin: Option<ChildStdin>,
}

fn engine() -> Vec<String> {
    if let Ok(cmd) = env::var("HUNT_ENGINE") {
        return cmd.split_whitespace().map(String::from).collect();
    }
    let dir = env::current_exe().ok().and_then(|p| p.parent().map(Path::to_path_buf)).unwrap_or_default();
    vec![dir.join(if cfg!(windows) { "hunt-engine.exe" } else { "hunt-engine" }).to_string_lossy().into()]
}

// ---------------------------------------------------------------- the window

struct Hunt {
    paths: Paths,
    settings: Map<String, Value>,
    tx: Sender<Msg>,
    rx: Receiver<Msg>,
    ctx: egui::Context,
    log: Option<File>,

    proc: Option<Proc>,
    next_id: u64,
    mode: String,
    state: Map<String, Value>,
    code: String,
    last_line: String,
    last_exit: Option<&'static str>,
    restart_to: Option<String>,
    test: bool,
    reports: bool,
    fake: bool, // --smoke: a hunter that isn't there

    hook: String,
    hook_edit: String,
    hook_open: bool,
    hook_note: Option<(&'static str, Instant)>,
    ball: String,
    ball_edit: String,

    update: Option<Map<String, Value>>,
    note: String,
    update_sure: bool,
    installer: Option<PathBuf>,
    install_when_stopped: bool,

    open: bool,
    slide: Option<(Instant, f32, f32)>, // started, from, to
    extra: f32,
    lift: f32,
    pos: Option<Pos2>,
    pos_saved: Option<Pos2>,
    pos_changed: Option<Instant>,
    sized: f32,
    was_found: String,
    smoke: Option<Smoke>,
    start_now: bool, // --start: play pressed at once
}

impl Hunt {
    fn new(cc: &eframe::CreationContext) -> Hunt {
        let paths = Paths::new();
        if !paths.config.exists() {
            // no rules: every reskin with a gradient is caught all the same
            let cfg = json!({"webhook": "", "ping": "@here", "targets": [], "notify": []});
            write_json(&paths.config, cfg.as_object().unwrap());
        }
        if fs::metadata(&paths.log).map(|m| m.len() > 5 << 20).unwrap_or(false) {
            let _ = fs::rename(&paths.log, paths.data.join("hunt.old.log"));
        }
        let log = OpenOptions::new().create(true).append(true).open(&paths.log).ok();
        let settings = read_json(&paths.settings).unwrap_or_default();
        let pos = settings.get("pos").and_then(Value::as_array).and_then(|a| {
            Some(pos2(a.first()?.as_f64()? as f32, a.get(1)?.as_f64()? as f32))
        });
        setup_fonts(&cc.egui_ctx);
        let mut visuals = egui::Visuals::dark();
        visuals.panel_fill = Color32::TRANSPARENT;
        visuals.window_fill = Color32::TRANSPARENT;
        visuals.selection.bg_fill = gray(0x55);
        visuals.text_cursor.stroke = Stroke::new(1.5_f32, PAPER);
        cc.egui_ctx.set_visuals(visuals);

        let (tx, rx) = channel();
        let mut hunt = Hunt {
            reports: settings.get("reports").and_then(Value::as_bool).unwrap_or(true),
            settings,
            tx,
            rx,
            ctx: cc.egui_ctx.clone(),
            log,
            proc: None,
            next_id: 0,
            mode: String::new(),
            state: Map::new(),
            code: String::new(),
            last_line: String::new(),
            last_exit: None,
            restart_to: None,
            test: false,
            fake: false,
            hook: String::new(),
            hook_edit: String::new(),
            hook_open: false,
            hook_note: None,
            ball: String::new(),
            ball_edit: String::new(),
            update: None,
            note: String::new(),
            update_sure: false,
            installer: None,
            install_when_stopped: false,
            open: false,
            slide: None,
            extra: 0.0,
            lift: 0.0,
            pos,
            pos_saved: pos,
            pos_changed: None,
            sized: H,
            was_found: String::new(),
            smoke: env::args().any(|a| a == "--smoke").then(Smoke::new),
            start_now: env::args().any(|a| a == "--start"),
            paths,
        };
        hunt.hook = hunt.hook_saved();
        hunt.ball = hunt.ball_saved();
        hunt.ball_edit = hunt.ball.clone();
        if version() > 0 {
            hunt.watch_updates();
        }
        hunt
    }

    fn running(&self) -> bool {
        self.proc.is_some() || self.fake
    }

    fn say(&mut self, line: &str) {
        if let Some(f) = self.log.as_mut() {
            let _ = writeln!(f, "{line}");
        }
    }

    // ------------------------------------------------------------ the hunter

    fn start(&mut self, mode: &str) {
        if self.running() {
            return;
        }
        let mut cmd = engine();
        cmd.extend(match mode {
            "watch" => vec!["--watch"],
            "debug" => vec!["--debug"],
            "diagnose" => vec!["--diagnose"],
            _ => vec![],
        }.into_iter().map(String::from));
        if self.test {
            cmd.extend(["--practice".into(), "1".into()]);
        }
        cmd.extend(["--window".into(), "--config".into(), self.paths.config.to_string_lossy().into()]);
        let mut command = Command::new(&cmd[0]);
        command.args(&cmd[1..]).current_dir(&self.paths.data)
            .env("HUNT_DATA", &self.paths.data).env("PYTHONUNBUFFERED", "1").env("PYTHONIOENCODING", "utf-8")
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
        if !self.reports {
            command.env("HUNT_DEBUG", "0");
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        }
        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(e) => {
                self.say(&format!("couldn't start the hunter ({}): {e}", cmd[0]));
                self.last_exit = Some("No hunter");
                return;
            }
        };
        self.next_id += 1;
        let id = self.next_id;
        let (out, err, stdin) = (child.stdout.take(), child.stderr.take(), child.stdin.take());
        let child = Arc::new(Mutex::new(child));
        let reader = |pipe: Box<dyn Read + Send>, tx: Sender<Msg>, ctx: egui::Context| {
            thread::spawn(move || {
                let mut pipe = BufReader::new(pipe);
                let mut buf = Vec::new();
                while pipe.read_until(b'\n', &mut buf).map(|n| n > 0).unwrap_or(false) {
                    let line = String::from_utf8_lossy(&buf).trim_end_matches(['\r', '\n']).to_string();
                    buf.clear();
                    if tx.send(Msg::Line(id, line)).is_err() {
                        break;
                    }
                    ctx.request_repaint();
                }
            })
        };
        let err_thread = err.map(|e| reader(Box::new(e), self.tx.clone(), self.ctx.clone()));
        let out_thread = out.map(|o| reader(Box::new(o), self.tx.clone(), self.ctx.clone()));
        let (tx, ctx, waiting) = (self.tx.clone(), self.ctx.clone(), child.clone());
        thread::spawn(move || {
            for t in [out_thread, err_thread].into_iter().flatten() {
                let _ = t.join();
            }
            let ok = loop {
                if let Ok(Some(status)) = waiting.lock().unwrap().try_wait() {
                    break status.success();
                }
                thread::sleep(Duration::from_millis(50));
            };
            let _ = tx.send(Msg::Ended(id, ok));
            ctx.request_repaint();
        });
        self.proc = Some(Proc { id, child, stdin });
        self.mode = mode.into();
        self.state.clear();
        self.code.clear();
        self.last_exit = None;
        let stamp = chrono_now();
        self.say(&format!("\n--- {stamp} {mode} (build {NAME}) ---"));
    }

    fn send(&mut self, cmd: &str) {
        if let Some(stdin) = self.proc.as_mut().and_then(|p| p.stdin.as_mut()) {
            let _ = writeln!(stdin, "{cmd}").and_then(|_| stdin.flush());
        }
    }

    /// Its input closed, it stops; killed if it's still there 6 s later.
    fn stop(&mut self) {
        self.fake = false;
        if let Some(proc) = self.proc.as_mut() {
            proc.stdin = None;
            let child = proc.child.clone();
            thread::spawn(move || {
                thread::sleep(Duration::from_secs(6));
                let _ = child.lock().unwrap().kill();
            });
        }
    }

    /// New settings: a hunt that's on starts again with them, once the old one is gone.
    fn restart(&mut self) {
        if self.proc.is_some() && (self.mode == "hunt" || self.mode == "debug" || self.mode == "watch") {
            self.restart_to = Some(self.mode.clone());
            self.stop();
        }
    }

    fn got_line(&mut self, line: String) {
        if let Some(state) = line.strip_prefix("@state ") {
            if let Ok(Value::Object(s)) = serde_json::from_str(state) {
                self.got_state(s);
            }
            return;
        }
        if let Some(i) = line.find("Website code: ") {
            let code: String = line[i + 14..].chars().take_while(|c| c.is_alphanumeric()).collect();
            if !code.is_empty() {
                self.code = code;
                return; // the code isn't for hunt.log (it gets sent as problem reports)
            }
        }
        if line.contains("a browser logged in with the code") {
            self.code.clear();
        }
        if !line.trim().is_empty() {
            self.last_line = line.trim().to_string();
            self.say(&line);
        }
    }

    fn got_state(&mut self, s: Map<String, Value>) {
        let flag = |k: &str| s.get(k).and_then(Value::as_bool).unwrap_or(false);
        if let Some(test) = s.get("test").and_then(Value::as_bool) {
            self.test = test; // off once its encounter is caught
        }
        let enc = s.get("enc").and_then(Value::as_str).unwrap_or("").to_string();
        let found = s.get("phase").and_then(Value::as_str) == Some("battle") && flag("wanted");
        if found && self.was_found != enc {
            // it's holding the battle: catch it in Roblox, then play to carry on
            self.say(&format!("Found: {enc}. Catch it in Roblox, then press play."));
            self.ctx.send_viewport_cmd(ViewportCommand::Focus);
            self.ctx.send_viewport_cmd(ViewportCommand::RequestUserAttention(egui::UserAttentionType::Informational));
            #[cfg(windows)]
            unsafe {
                windows_sys::Win32::System::Diagnostics::Debug::MessageBeep(0);
            }
        }
        self.was_found = if found { enc } else { String::new() };
        self.state = s;
    }

    fn ended(&mut self, ok: bool) {
        self.last_exit = if self.mode == "diagnose" {
            Some("Done") // what it found is in hunt.log
        } else if !ok && !self.last_line.is_empty() && !self.last_line.contains("stopped") {
            Some("Stopped") // why is in hunt.log
        } else {
            None
        };
        self.proc = None;
        self.state.clear();
        self.code.clear();
        self.test = false;
        self.was_found.clear();
        if self.install_when_stopped {
            self.install();
        } else if let Some(mode) = self.restart_to.take() {
            self.start(&mode);
        }
    }

    fn found_now(&self) -> bool {
        let s = &self.state;
        self.running() && s.get("phase").and_then(Value::as_str) == Some("battle")
            && s.get("wanted").and_then(Value::as_bool).unwrap_or(false)
            && !s.get("remote").and_then(Value::as_bool).unwrap_or(false)
    }

    /// Play: not hunting, or paused (by a find, or Ctrl+Shift+H). Otherwise stop.
    fn shows_play(&self) -> bool {
        let s = &self.state;
        !self.running()
            || (!s.is_empty() && !s.get("running").and_then(Value::as_bool).unwrap_or(false)
                && !s.get("remote").and_then(Value::as_bool).unwrap_or(false) && self.mode != "diagnose")
    }

    fn main_button(&mut self) {
        if !self.running() {
            let mode = if env::var_os("HUNT_WATCH").is_some() { "watch" } else { "hunt" };
            self.start(mode);
        } else if self.shows_play() {
            self.send("toggle");
        } else {
            self.stop();
        }
    }

    fn code_text(&self) -> String {
        let web = self.running() && matches!(self.mode.as_str(), "hunt" | "debug" | "calibrate");
        if web && !self.code.is_empty() {
            self.code.clone()
        } else if self.running() {
            String::new()
        } else {
            self.last_exit.unwrap_or("").into()
        }
    }

    // ------------------------------------------------------------ settings in config.json

    fn hook_saved(&self) -> String {
        read_json(&self.paths.config)
            .and_then(|c| c.get("webhook").and_then(Value::as_str).map(String::from))
            .unwrap_or_default()
    }

    fn hook_done(&mut self) {
        self.hook_open = false;
        let url = self.hook_edit.trim().to_string();
        if url == self.hook {
            return;
        }
        if !url.is_empty() && !is_webhook(&url) {
            self.hook_note = Some(("Not a webhook", Instant::now()));
            return;
        }
        let Some(mut cfg) = read_json(&self.paths.config) else {
            self.hook_note = Some(("config.json has a mistake", Instant::now()));
            return;
        };
        cfg.insert("webhook".into(), Value::String(url.clone()));
        write_json(&self.paths.config, &cfg);
        self.hook = url;
        self.restart();
    }

    fn ball_saved(&self) -> String {
        let cfg = read_json(&self.paths.config).unwrap_or_default();
        let catch = cfg.get("catch").and_then(Value::as_object);
        let get = |k: &str| catch.and_then(|c| c.get(k)).and_then(Value::as_str).map(String::from);
        if get("first_ball").as_deref() == Some("Park Ball") {
            return "Park Ball".into();
        }
        get("awake_ball").unwrap_or_else(|| "Repeat Ball".into())
    }

    /// The preferred ball, in config.json's catch plan: a Quick Ball first,
    /// then this one. A Park Ball is the only ball (it never misses).
    fn ball_done(&mut self) {
        let ball = ball_name(&self.ball_edit);
        self.ball_edit = ball.clone();
        if ball == self.ball {
            return;
        }
        let Some(mut cfg) = read_json(&self.paths.config) else {
            self.ball_edit = self.ball.clone();
            return;
        };
        let mut catch = cfg.get("catch").and_then(Value::as_object).cloned().unwrap_or_default();
        for k in ["first_ball", "awake_ball", "asleep_ball", "new_ball"] {
            catch.remove(k); // the defaults: a Quick Ball, then Repeat Balls (Dive Balls for new ones)
        }
        let keys: &[&str] = if ball == "Park Ball" {
            &["first_ball", "awake_ball", "asleep_ball", "new_ball"]
        } else if ball != "Repeat Ball" {
            &["awake_ball", "asleep_ball", "new_ball"]
        } else {
            &[]
        };
        for k in keys {
            catch.insert((*k).into(), Value::String(ball.clone()));
        }
        cfg.insert("catch".into(), Value::Object(catch));
        write_json(&self.paths.config, &cfg);
        self.ball = ball;
        self.restart();
    }

    fn flip_test(&mut self) {
        self.test = !self.test;
        let cmd = if self.test { "test on" } else { "test off" };
        self.send(cmd);
    }

    // ------------------------------------------------------------ updates

    /// Once at the start and then every 6 hours: a newer build on the website?
    fn watch_updates(&self) {
        let (tx, ctx) = (self.tx.clone(), self.ctx.clone());
        thread::spawn(move || loop {
            let info = ureq::get(&format!("{}/windows.json", site()))
                .set("User-Agent", &format!("Hunt (windows, {NAME})"))
                .timeout(Duration::from_secs(15))
                .call()
                .ok()
                .and_then(|r| r.into_string().ok())
                .and_then(|t| serde_json::from_str::<Value>(&t).ok());
            if let Some(Value::Object(info)) = info {
                if info.get("version").and_then(Value::as_u64).unwrap_or(0) > version() {
                    let _ = tx.send(Msg::Update(info));
                    ctx.request_repaint();
                }
            }
            thread::sleep(Duration::from_secs(6 * 3600));
        });
    }

    fn update_button(&mut self) {
        let Some(info) = self.update.clone() else { return };
        if !self.note.is_empty() {
            return; // on its way
        }
        if self.running() && !self.update_sure {
            self.update_sure = true; // a second click: it stops the hunt for a minute
            return;
        }
        self.note = "Downloading".into();
        let (tx, ctx) = (self.tx.clone(), self.ctx.clone());
        thread::spawn(move || {
            let result = (|| -> Result<PathBuf, String> {
                let file = info.get("file").and_then(Value::as_str).unwrap_or("HuntSetup.exe");
                let number = info.get("version").and_then(Value::as_u64).unwrap_or(0);
                let path = env::temp_dir().join(format!("HuntSetup-{number}.exe"));
                let part = path.with_extension("part");
                let resp = ureq::get(&format!("{}/{file}", site()))
                    .set("User-Agent", &format!("Hunt (windows, {NAME})"))
                    .timeout(Duration::from_secs(300))
                    .call()
                    .map_err(|e| e.to_string())?;
                let total: u64 = resp.header("Content-Length").and_then(|v| v.parse().ok()).unwrap_or(0);
                let mut body = resp.into_reader();
                let mut out = File::create(&part).map_err(|e| e.to_string())?;
                let (mut digest, mut got, mut buf) = (Sha256::new(), 0u64, vec![0u8; 1 << 16]);
                loop {
                    let n = body.read(&mut buf).map_err(|e| e.to_string())?;
                    if n == 0 {
                        break;
                    }
                    out.write_all(&buf[..n]).map_err(|e| e.to_string())?;
                    digest.update(&buf[..n]);
                    got += n as u64;
                    if total > 0 {
                        let _ = tx.send(Msg::Progress(format!("{}%", 100 * got / total)));
                        ctx.request_repaint();
                    }
                }
                drop(out);
                let hex: String = digest.finalize().iter().map(|b| format!("{b:02x}")).collect();
                if info.get("sha256").and_then(Value::as_str).is_some_and(|want| want != hex) {
                    return Err("the download came out different from the website's".into());
                }
                fs::rename(&part, &path).map_err(|e| e.to_string())?;
                Ok(path)
            })();
            let _ = tx.send(match result {
                Ok(path) => Msg::Downloaded(path),
                Err(e) => Msg::UpdateFailed(e),
            });
            ctx.request_repaint();
        });
    }

    /// The installer replaces Hunt's folder and starts the new Hunt; this one gets out of its way.
    fn install(&mut self) {
        #[cfg(windows)]
        if let Some(setup) = &self.installer {
            use std::os::windows::process::CommandExt;
            let _ = Command::new(setup)
                .args(["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
                .creation_flags(0x0000_0008) // DETACHED_PROCESS
                .spawn();
        }
        self.ctx.send_viewport_cmd(ViewportCommand::Close);
    }

    // ------------------------------------------------------------ the window's place and size

    fn monitor(&self, ctx: &egui::Context) -> Vec2 {
        ctx.input(|i| i.viewport().monitor_size).unwrap_or(vec2(1920.0, 1080.0))
    }

    fn more(&mut self, ctx: &egui::Context) {
        self.open = !self.open;
        let full = H + PANEL;
        if self.open {
            // the window gets the panel's room first, then the pill grows into it
            ctx.send_viewport_cmd(ViewportCommand::InnerSize(vec2(W, full)));
            self.sized = full;
            if let Some(pos) = self.pos {
                self.lift = (pos.y + full - (self.monitor(ctx).y - 48.0)).max(0.0);
                if self.lift > 0.0 {
                    ctx.send_viewport_cmd(ViewportCommand::OuterPosition(pos2(pos.x, pos.y - self.lift)));
                }
            }
        }
        self.slide = Some((Instant::now(), self.extra, if self.open { PANEL } else { 0.0 }));
    }

    fn animate(&mut self, ctx: &egui::Context) {
        let Some((t0, from, to)) = self.slide else { return };
        let t = (t0.elapsed().as_secs_f32() / SLIDE).min(1.0);
        let k = if t < 0.5 { 4.0 * t * t * t } else { 1.0 - (-2.0 * t + 2.0).powi(3) / 2.0 }; // eased in and out
        self.extra = from + (to - from) * k;
        if t < 1.0 {
            ctx.request_repaint();
            return;
        }
        self.slide = None;
        if !self.open {
            ctx.send_viewport_cmd(ViewportCommand::InnerSize(vec2(W, H)));
            self.sized = H;
            if self.lift > 0.0 {
                if let Some(pos) = self.pos {
                    ctx.send_viewport_cmd(ViewportCommand::OuterPosition(pos));
                }
                self.lift = 0.0;
            }
        }
    }

    /// Where the pill is: followed as it's dragged, kept in window.json.
    fn track_place(&mut self, ctx: &egui::Context) {
        let Some(outer) = ctx.input(|i| i.viewport().outer_rect) else { return };
        let screen = self.monitor(ctx);
        if self.pos.is_some_and(|p| p.x < -W / 2.0 || p.x > screen.x - W / 2.0 || p.y < 0.0 || p.y > screen.y - H) {
            self.pos = None; // the screen it was on is gone
        }
        if self.pos.is_none() {
            // first time on this PC: the top middle, room under it for the panel
            let at = pos2(((screen.x - W) / 2.0).round(), 60.0);
            ctx.send_viewport_cmd(ViewportCommand::OuterPosition(at));
            self.pos = Some(at);
            return;
        }
        let at = pos2(outer.min.x, outer.min.y + self.lift);
        if self.slide.is_none() && Some(at) != self.pos && (at - self.pos.unwrap()).length() > 0.5 {
            self.pos = Some(at);
            self.pos_changed = Some(Instant::now());
        }
        if self.pos_changed.is_some_and(|t| t.elapsed() > Duration::from_millis(800)) && self.pos != self.pos_saved {
            let p = self.pos.unwrap();
            self.settings.insert("pos".into(), json!([p.x.round(), p.y.round()]));
            write_json(&self.paths.settings, &self.settings);
            self.pos_saved = self.pos;
            self.pos_changed = None;
        }
    }

    // ------------------------------------------------------------ drawing

    fn draw(&mut self, ui: &mut egui::Ui) {
        let ctx = ui.ctx().clone();
        let painter = ui.painter().clone();
        let light = self.found_now();
        let c = look(light);
        let r = H / 2.0;

        // the outline, as tall as it's grown; dragged by anything that isn't a button
        let shape = Rect::from_min_size(Pos2::ZERO, vec2(W, H + self.extra));
        painter.rect_filled(shape, r, c.edge); // a hairline around it, for dark games
        painter.rect_filled(shape.shrink(1.0), r - 1.0, c.fill);
        let bg = ui.interact(shape, Id::new("bg"), Sense::click_and_drag());
        if bg.drag_started() {
            ctx.send_viewport_cmd(ViewportCommand::StartDrag);
        }
        if bg.clicked() {
            ctx.memory_mut(|m| {
                m.surrender_focus(Id::new("hook"));
                m.surrender_focus(Id::new("ball"));
            });
        }

        // ---- the pill: start / stop, the website code, the webhook box, •••
        let cy = H / 2.0;
        let main = pos2(25.0, cy);
        let resp = ui.interact(Rect::from_center_size(main, vec2(34.0, 34.0)), Id::new("main"), Sense::click());
        painter.circle_filled(main, 17.0, if resp.hovered() { c.dim } else { c.ink });
        if self.shows_play() {
            let pts = vec![main + vec2(-4.0, -6.5), main + vec2(-4.0, 6.5), main + vec2(7.0, 0.0)];
            painter.add(egui::Shape::convex_polygon(pts, c.fill, Stroke::NONE));
        } else {
            painter.rect_filled(Rect::from_center_size(main, vec2(11.0, 11.0)), 1.0, c.fill);
        }
        if resp.on_hover_cursor(CursorIcon::PointingHand).clicked() {
            self.main_button();
        }

        let left = main.x + 31.0;
        let words = self.code_text();
        let shown = if words.is_empty() { "------".to_string() } else { words.clone() };
        let is_code = !self.code.is_empty() && shown == self.code;
        let code_font = FontId::new(15.0, FontFamily::Name("code".into()));
        let area = painter.text(pos2(left, cy), Align2::LEFT_CENTER, &shown, code_font.clone(), Color32::TRANSPARENT);
        let resp = ui.interact(area.expand(4.0), Id::new("code"), Sense::click());
        let color = if is_code && (light || resp.hovered()) {
            c.ink
        } else if is_code {
            gray(0xd8)
        } else if words.is_empty() {
            c.edge
        } else {
            c.dim
        };
        painter.text(pos2(left, cy), Align2::LEFT_CENTER, &shown, code_font, color);
        if is_code && resp.on_hover_cursor(CursorIcon::PointingHand).clicked() {
            open_path(&format!("{}/app", site()));
        }

        let more = pos2(W - 28.0, cy);
        let resp = ui.interact(Rect::from_center_size(more, vec2(30.0, 30.0)), Id::new("more"), Sense::click());
        if resp.hovered() || self.open {
            painter.circle_filled(more, 15.0, c.hover);
        }
        for dx in [-6.0, 0.0, 6.0] {
            painter.circle_filled(more + vec2(dx, 0.0), 1.8, c.ink);
        }
        if self.update.is_some() {
            painter.circle_filled(more + vec2(10.0, -9.0), 2.4, c.ink); // an update waits in the panel
        }
        if resp.on_hover_cursor(CursorIcon::PointingHand).clicked() {
            self.more(&ctx);
        }

        let hook_box = Rect::from_min_max(pos2(left + 92.0, cy - 14.0), pos2(more.x - 20.0, cy + 14.0));
        painter.rect_filled(hook_box, 14.0, c.boxed);
        let inner = hook_box.shrink2(vec2(12.0, 3.0));
        let text_font = FontId::proportional(14.0);
        if self.hook_open {
            let edit = egui::TextEdit::singleline(&mut self.hook_edit)
                .id(Id::new("hook"))
                .frame(false)
                .font(text_font)
                .text_color(c.ink)
                .margin(egui::Margin::ZERO)
                .vertical_align(egui::Align::Center);
            let resp = ui.put(inner, edit);
            if ctx.input(|i| i.key_pressed(egui::Key::Escape)) {
                self.hook_edit = self.hook.clone();
            }
            if resp.lost_focus() {
                self.hook_done();
            }
        } else {
            let note = self.hook_note.filter(|(_, t)| t.elapsed() < Duration::from_millis(2500));
            if self.hook_note.is_some() && note.is_none() {
                self.hook_note = None;
            }
            if note.is_some() {
                ctx.request_repaint_after(Duration::from_millis(300));
            }
            let words = note.map(|(n, _)| n).unwrap_or(if self.hook.is_empty() { "Discord webhook" } else { "Webhook on" });
            painter.text(pos2(inner.min.x, cy), Align2::LEFT_CENTER, words, text_font, c.dim);
            let resp = ui.interact(hook_box, Id::new("hookbox"), Sense::click());
            if resp.on_hover_cursor(CursorIcon::Text).clicked() {
                self.hook_open = true;
                self.hook_edit = self.hook.clone();
                ctx.memory_mut(|m| m.request_focus(Id::new("hook")));
            }
        }

        // ---- the panel, inside the same outline
        let settled = self.open && self.slide.is_none_or(|(t0, _, _)| t0.elapsed().as_secs_f32() > SLIDE * 0.8);
        if !settled {
            return;
        }
        let pad = 22.0;
        painter.line_segment([pos2(pad, H), pos2(W - pad, H)], Stroke::new(1.0_f32, c.edge));
        let label = FontId::proportional(14.0);
        let row = |i: f32| H + 6.0 + ROW * (i + 0.5);

        let y = row(0.0);
        painter.text(pos2(pad, y), Align2::LEFT_CENTER, "Debug mode", label.clone(), c.ink);
        let track = Rect::from_min_max(pos2(W - pad - 40.0, y - 11.0), pos2(W - pad, y + 11.0));
        let resp = ui.interact(track, Id::new("test"), Sense::click());
        let (fill, knob) = if self.test { (c.ink, c.fill) } else { (c.edge, c.dim) };
        painter.rect_filled(track, 11.0, fill);
        painter.circle_filled(pos2(if self.test { track.max.x - 11.0 } else { track.min.x + 11.0 }, y), 8.0, knob);
        if resp.on_hover_cursor(CursorIcon::PointingHand).clicked() {
            self.flip_test();
        }

        let y = row(1.0);
        painter.text(pos2(pad, y), Align2::LEFT_CENTER, "Preferred ball", label.clone(), c.ink);
        let ball_box = Rect::from_min_max(pos2(W - pad - 150.0, y - 14.0), pos2(W - pad, y + 14.0));
        painter.rect_filled(ball_box, 14.0, c.boxed);
        let edit = egui::TextEdit::singleline(&mut self.ball_edit)
            .id(Id::new("ball"))
            .frame(false)
            .font(label.clone())
            .text_color(c.ink)
            .margin(egui::Margin::ZERO)
            .vertical_align(egui::Align::Center);
        if ui.put(ball_box.shrink2(vec2(12.0, 3.0)), edit).lost_focus() {
            self.ball_done();
        }

        // Update, Folder, Quit: one row of pills
        let y = row(1.0) + ROW + 2.0;
        let (gap, bw) = (10.0, (W - 2.0 * pad - 20.0) / 3.0);
        let update_words = if self.update.is_none() {
            "Up to date".to_string()
        } else if !self.note.is_empty() {
            self.note.clone()
        } else if self.update_sure {
            "Stops the hunt".into()
        } else {
            "Update".into()
        };
        for (i, (id, words)) in [("update", update_words.as_str()), ("folder", "Folder"), ("quit", "Quit")]
            .into_iter()
            .enumerate()
        {
            let b = Rect::from_min_size(pos2(pad + i as f32 * (bw + gap), y - 16.0), vec2(bw, 32.0));
            let on = id != "update" || self.update.is_some();
            let filled = id == "update" && self.update.is_some();
            let resp = ui.interact(b, Id::new(id), if on { Sense::click() } else { Sense::hover() });
            painter.rect_filled(b, 16.0, if filled { c.ink } else { c.boxed });
            let color = if filled { c.fill } else if !on { c.edge } else if resp.hovered() { c.ink } else { gray(0xc8) };
            painter.text(b.center(), Align2::CENTER_CENTER, words, label.clone(), if light && !filled && on { c.ink } else { color });
            if on && resp.on_hover_cursor(CursorIcon::PointingHand).clicked() {
                match id {
                    "update" => self.update_button(),
                    "folder" => open_path(&self.paths.data.to_string_lossy()),
                    _ => self.quit(&ctx),
                }
            }
        }
    }

    fn quit(&mut self, ctx: &egui::Context) {
        self.stop();
        ctx.send_viewport_cmd(ViewportCommand::Close);
    }
}

impl eframe::App for Hunt {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        while let Ok(msg) = self.rx.try_recv() {
            match msg {
                Msg::Line(id, line) if self.proc.as_ref().map(|p| p.id) == Some(id) => self.got_line(line),
                Msg::Ended(id, ok) if self.proc.as_ref().map(|p| p.id) == Some(id) => self.ended(ok),
                Msg::Line(..) | Msg::Ended(..) => {}
                Msg::Update(info) => self.update = Some(info),
                Msg::Progress(p) => self.note = p,
                Msg::UpdateFailed(e) => {
                    self.say(&format!("the update didn't download: {e}"));
                    self.note.clear();
                    self.update_sure = false;
                }
                Msg::Downloaded(path) => {
                    self.installer = Some(path);
                    self.note = "Installing".into();
                    if self.proc.is_some() {
                        self.install_when_stopped = true;
                        self.stop(); // ended() installs, once the hunter is gone
                    } else {
                        self.install();
                    }
                }
            }
        }
        if ctx.input(|i| i.viewport().close_requested()) {
            self.stop();
        }
        self.animate(ctx);
        self.track_place(ctx);
        if std::mem::take(&mut self.start_now) {
            self.main_button();
        }
        if let Some(mut smoke) = self.smoke.take() {
            smoke.step(self, ctx);
            self.smoke = Some(smoke);
        }
        egui::CentralPanel::default().frame(egui::Frame::default()).show(ctx, |ui| self.draw(ui));
    }

    fn clear_color(&self, _visuals: &egui::Visuals) -> [f32; 4] {
        [0.0, 0.0, 0.0, 0.0] // see-through, but for the pill
    }
}

/// The local time, for hunt.log's start lines.
fn chrono_now() -> String {
    #[cfg(windows)]
    unsafe {
        let mut t: windows_sys::Win32::Foundation::SYSTEMTIME = std::mem::zeroed();
        windows_sys::Win32::System::SystemInformation::GetLocalTime(&mut t);
        format!("{}-{:02}-{:02} {:02}:{:02}:{:02}", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond)
    }
    #[cfg(not(windows))]
    {
        Command::new("date").arg("+%Y-%m-%d %H:%M:%S").output()
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()).unwrap_or_default()
    }
}

// ---------------------------------------------------------------- fonts

fn setup_fonts(ctx: &egui::Context) {
    let mut fonts = egui::FontDefinitions::default();
    let mut add = |name: &str, paths: &[(&str, u32)], family: FontFamily| {
        for (path, index) in paths {
            if let Ok(bytes) = fs::read(path) {
                let mut data = egui::FontData::from_owned(bytes);
                data.index = *index;
                fonts.font_data.insert(name.into(), Arc::new(data));
                fonts.families.entry(family.clone()).or_default().insert(0, name.into());
                return;
            }
        }
    };
    add("ui", &[("C:\\Windows\\Fonts\\segoeui.ttf", 0), ("/System/Library/Fonts/Helvetica.ttc", 0)],
        FontFamily::Proportional);
    add("code", &[("C:\\Windows\\Fonts\\consolab.ttf", 0), ("/System/Library/Fonts/Menlo.ttc", 1)],
        FontFamily::Name("code".into()));
    // the code's family needs something even where neither font is
    let fallback = fonts.families[&FontFamily::Monospace].clone();
    fonts.families.entry(FontFamily::Name("code".into())).or_default().extend(fallback);
    ctx.set_fonts(fonts);
}

// ---------------------------------------------------------------- --smoke

/// The Windows build's check (--smoke): the pill comes up and goes through its
/// states, the webhook box, the panel and the preferred ball, then closes.
/// smoke.txt says how it went; anything wrong ends Hunt.exe with 1.
struct Smoke {
    t0: Instant,
    stage: usize,
    errors: Vec<String>,
}

impl Smoke {
    fn new() -> Smoke {
        Smoke { t0: Instant::now(), stage: 0, errors: Vec::new() }
    }

    fn step(&mut self, h: &mut Hunt, ctx: &egui::Context) {
        ctx.request_repaint();
        let at = self.t0.elapsed().as_secs_f32();
        let state = |v: Value| v.as_object().cloned().unwrap_or_default();
        let stages: &[f32] = &[0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 3.4, 4.0];
        if self.stage >= stages.len() || at < stages[self.stage] {
            return;
        }
        match self.stage {
            0 => { h.fake = true; h.mode = "hunt".into(); h.got_state(state(json!({}))); }
            1 => { h.code = "AB12C".into(); h.got_state(state(json!({"running": true, "phase": "waiting"}))); }
            2 => h.got_state(state(json!({"running": false, "phase": "battle", "wanted": true, "enc": "Shiny Tympole"}))),
            3 => h.got_state(state(json!({"running": false, "phase": "waiting"}))),
            4 => {
                h.fake = false;
                h.state.clear();
                for url in ["not a webhook", "https://discord.com/api/webhooks/1/smoke", ""] {
                    h.hook_edit = url.into();
                    h.hook_done();
                }
                if h.hook_saved() != "" {
                    self.errors.push("the webhook didn't clear".into());
                }
            }
            5 => {
                h.update = Some(state(json!({"version": version() + 1, "name": "smoke"})));
                h.more(ctx);
            }
            6 => { h.flip_test(); h.flip_test(); }
            7 => {
                let mut saved = Vec::new();
                for ball in ["park", "ultra ball", ""] {
                    h.ball_edit = ball.into();
                    h.ball_done();
                    let cfg = read_json(&h.paths.config).unwrap_or_default();
                    saved.push(cfg.get("catch").and_then(|c| c.get("awake_ball")).and_then(Value::as_str)
                        .unwrap_or("-").to_string());
                }
                if saved != ["Park Ball", "Ultra Ball", "-"] {
                    self.errors.push(format!("the preferred ball saved as {saved:?}"));
                }
            }
            8 => {} // open, for the build's picture
            9 => {
                let outer = ctx.input(|i| i.viewport().outer_rect).unwrap_or(Rect::NOTHING);
                let words = if self.errors.is_empty() {
                    format!("ok: the pill is {} x {} at {}, {}\n", outer.width(), outer.height(), outer.min.x, outer.min.y)
                } else {
                    self.errors.join("\n") + "\n"
                };
                let _ = fs::write(h.paths.data.join("smoke.txt"), words);
            }
            _ => std::process::exit(if self.errors.is_empty() { 0 } else { 1 }),
        }
        self.stage += 1;
    }
}

// ---------------------------------------------------------------- main

/// One Hunt at a time: two would fight over the mouse.
fn single_instance() -> bool {
    #[cfg(windows)]
    unsafe {
        use windows_sys::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS};
        use windows_sys::Win32::System::Threading::CreateMutexW;
        let name: Vec<u16> = "Local\\HuntWindow\0".encode_utf16().collect();
        CreateMutexW(std::ptr::null(), 0, name.as_ptr());
        if GetLastError() == ERROR_ALREADY_EXISTS {
            use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONINFORMATION};
            let text: Vec<u16> = "Hunt is already open: look for the Poke Ball in the taskbar.\0".encode_utf16().collect();
            let title: Vec<u16> = "Hunt\0".encode_utf16().collect();
            MessageBoxW(std::ptr::null_mut(), text.as_ptr(), title.as_ptr(), MB_ICONINFORMATION);
            return false;
        }
        let id: Vec<u16> = "poopihunt.Hunt\0".encode_utf16().collect();
        windows_sys::Win32::UI::Shell::SetCurrentProcessExplicitAppUserModelID(id.as_ptr()); // its own taskbar icon
    }
    true
}

fn main() -> eframe::Result {
    if !single_instance() {
        return Ok(());
    }
    let mut viewport = egui::ViewportBuilder::default()
        .with_title("Hunt")
        .with_inner_size([W, H])
        .with_decorations(false)
        .with_transparent(true)
        .with_resizable(false)
        .with_taskbar(true);
    if let Ok(icon) = eframe::icon_data::from_png_bytes(include_bytes!("../../site/app/icon-192.png")) {
        viewport = viewport.with_icon(Arc::new(icon));
    }
    let settings = read_json(&Paths::new().settings).unwrap_or_default();
    if let Some([x, y]) = settings.get("pos").and_then(Value::as_array).map(|a| {
        [a.first().and_then(Value::as_f64).unwrap_or(0.0) as f32, a.get(1).and_then(Value::as_f64).unwrap_or(0.0) as f32]
    }) {
        viewport = viewport.with_position([x, y]);
    }
    let options = eframe::NativeOptions { viewport, renderer: eframe::Renderer::Glow, ..Default::default() };
    eframe::run_native("Hunt", options, Box::new(|cc| Ok(Box::new(Hunt::new(cc)))))
}
