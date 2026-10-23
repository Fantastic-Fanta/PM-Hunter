//! What the hunter says: to the console, the window's hunt.log or the app's
//! screen (a Sink), to the website's log, and to the debug channel, which
//! gets everything in a debug run and otherwise only what goes wrong.

use crate::discord;
use crate::picture;
use crate::px::Frame;
use crate::util::{clip, random_hex, sleep};
use regex::Regex;
use serde_json::Value;
use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, LazyLock, Mutex};
use std::time::Duration;

/// Where the hunter's words go.
pub trait Sink: Send + Sync {
    /// A line the hunter says (it may be blank).
    fn line(&self, text: &str);
    /// The website's login code (None once it's spent). Never logged: it would
    /// reach the debug channel.
    fn code(&self, _code: Option<&str>) {}
    /// What the hunter's doing, for a window: {"running", "remote", "count",
    /// "phase", "wanted", "enc", "test"}.
    fn state(&self, _state: &Value) {}
}

/// To stdout (a Terminal's hunter, --diagnose, --image).
pub struct Console;

impl Sink for Console {
    fn line(&self, text: &str) {
        use std::io::Write;
        let mut out = std::io::stdout().lock();
        let _ = writeln!(out, "{text}");
        let _ = out.flush();
    }
    fn code(&self, code: Option<&str>) {
        if let Some(code) = code {
            self.line(&format!("\n  Website code: {code}   (type it on {}/app)\n", crate::web::site()));
        }
    }
}

pub struct Out {
    pub sink: Arc<dyn Sink>,
    pub debug: Arc<DebugChannel>,
    said: Mutex<Option<String>>,
    lines: Mutex<VecDeque<String>>,
}

impl Out {
    pub fn new(sink: Arc<dyn Sink>, debug: Arc<DebugChannel>) -> Out {
        Out { sink, debug, said: Mutex::new(None), lines: Mutex::new(VecDeque::new()) }
    }

    pub fn print(&self, msg: &str) {
        for line in msg.split('\n') {
            self.sink.line(line);
            if line.trim().is_empty() {
                continue;
            }
            self.debug.note(line);
            if !["  wanted:", "  auto-catch:", "  post only:"].iter().any(|p| line.starts_with(p)) {
                let mut lines = self.lines.lock().unwrap();
                lines.push_back(line.to_string());
                while lines.len() > 8 {
                    lines.pop_front();
                }
            }
        }
    }

    /// Print, but don't repeat the same waiting message every loop.
    pub fn say(&self, msg: &str) {
        let mut said = self.said.lock().unwrap();
        if said.as_deref() != Some(msg) {
            *said = Some(msg.to_string());
            drop(said);
            self.print(msg);
        }
    }

    pub fn reset_say(&self) {
        *self.said.lock().unwrap() = None;
    }

    /// The last lines said, for the website.
    pub fn recent(&self) -> Vec<String> {
        self.lines.lock().unwrap().iter().cloned().collect()
    }
}

// ---------------------------------------------------------------- the debug channel

/// a line saying something went wrong (the hunter's "trouble" always comes with a screenshot)
static FAILURE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)FAILED|went wrong|couldn't|could not|can't|didn't|failed|stuck|gave up|giving up|error|exception|traceback|screenshot:|crash").unwrap()
});

const SHOTS: usize = 25; // screenshots per run at most (besides crashes)
const CLICK_SHOTS: usize = 10; // a debug run's first clicks get a close-up each

/// What goes wrong on a run, to a Discord webhook: for following a run on
/// someone else's PC or phone. Not every encounter: a debug run (and --debug,
/// --diagnose) is sent in full, lines and pictures; otherwise only what went
/// wrong, a failure's line with the ones just before it and its picture, and
/// crashes. Lines are gathered and sent every few seconds, as code blocks.
pub struct DebugChannel {
    url: Mutex<String>,
    who: Mutex<String>,
    state: Mutex<Lines>,
    /// Everything's sent while it's on: a debug run.
    pub loud: AtomicBool,
    /// --debug: a picture of every click, not just the first few.
    pub every_shot: AtomicBool,
    shots: AtomicUsize,
    clicks: AtomicUsize,
    started: AtomicBool,
}

#[derive(Default)]
struct Lines {
    lines: Vec<String>,
    recent: VecDeque<String>, // lately, not sent: what led up to a failure
    header: String,           // the computer's details, with the run's first failure
}

impl DebugChannel {
    /// who: the system and build ("Windows 11 · 23 Oct 17:53"); a run number is added.
    pub fn new(url: &str, who: &str) -> Arc<DebugChannel> {
        let d = Arc::new(DebugChannel {
            url: Mutex::new(url.trim().to_string()),
            who: Mutex::new(format!("{who} · run {}", random_hex(2))),
            state: Mutex::new(Lines::default()),
            loud: AtomicBool::new(false),
            every_shot: AtomicBool::new(false),
            shots: AtomicUsize::new(0),
            clicks: AtomicUsize::new(0),
            started: AtomicBool::new(false),
        });
        d.start();
        d
    }

    pub fn off() -> Arc<DebugChannel> {
        DebugChannel::new("", "")
    }

    pub fn set_url(self: &Arc<Self>, url: &str) {
        *self.url.lock().unwrap() = url.trim().to_string();
        self.start();
    }

    pub fn is_on(&self) -> bool {
        !self.url.lock().unwrap().is_empty()
    }

    pub fn set_header(&self, header: &str) {
        self.state.lock().unwrap().header = header.to_string();
    }

    fn loud(&self) -> bool {
        self.loud.load(Ordering::Relaxed)
    }

    fn start(self: &Arc<Self>) {
        if !self.is_on() || self.started.swap(true, Ordering::SeqCst) {
            return;
        }
        let me = Arc::downgrade(self);
        let _ = std::thread::Builder::new().name("debug channel".into()).spawn(move || loop {
            std::thread::sleep(Duration::from_secs(4));
            match me.upgrade() {
                Some(d) => d.flush(),
                None => break,
            }
        });
        // anything that ends the run
        let me = Arc::downgrade(self);
        let old = std::panic::take_hook();
        std::panic::set_hook(Box::new(move |info| {
            if let Some(d) = me.upgrade() {
                let at = info.location().map(|l| format!(" at {}:{}", l.file(), l.line())).unwrap_or_default();
                let what = info.payload().downcast_ref::<&str>().map(|s| s.to_string())
                    .or_else(|| info.payload().downcast_ref::<String>().cloned()).unwrap_or_default();
                d.flush();
                d.post(&format!("crashed:\n```\n{}{at}\n```", clip(&what, 1700)), None);
            }
            old(info);
        }));
    }

    /// A line: sent in a debug run, or if it says something went wrong (with the
    /// lines just before it); kept for that otherwise.
    pub fn note(&self, text: &str) {
        if !self.is_on() {
            return;
        }
        let mut s = self.state.lock().unwrap();
        if self.loud() {
            s.lines.push(text.to_string());
        } else if FAILURE.is_match(text) {
            if !s.header.is_empty() {
                let h = std::mem::take(&mut s.header);
                s.lines.push(h);
            }
            let recent: Vec<String> = s.recent.drain(..).collect();
            s.lines.extend(recent);
            s.lines.push(text.to_string());
        } else {
            s.recent.push_back(text.to_string());
            while s.recent.len() > 15 {
                s.recent.pop_front();
            }
        }
    }

    pub fn flush(&self) {
        let lines = std::mem::take(&mut self.state.lock().unwrap().lines);
        let mut chunk = String::new();
        for line in lines {
            let line = clip(&line, 1800);
            if chunk.len() + line.len() > 1800 {
                self.post(&format!("```\n{chunk}```"), None);
                chunk.clear();
            }
            chunk.push_str(&line);
            chunk.push('\n');
        }
        if !chunk.trim().is_empty() {
            self.post(&format!("```\n{chunk}```"), None);
        }
    }

    /// A screenshot with a caption, up to 25 a run: of a failure, or anything in a debug run.
    pub fn shot(self: &Arc<Self>, caption: &str, img: &Frame, failure: bool) {
        if !self.is_on() || !(failure || self.loud()) {
            return;
        }
        if self.shots.fetch_add(1, Ordering::SeqCst) >= SHOTS && !self.every_shot.load(Ordering::Relaxed) {
            return;
        }
        let small = picture::at_most(img.clone(), 1280);
        self.flush(); // keep the order
        let (me, caption) = (self.clone(), caption.to_string());
        std::thread::spawn(move || me.post(&caption, Some(picture::png(&small))));
    }

    /// Whether this click gets a close-up (a debug run's first 10).
    pub fn click_shot(&self) -> bool {
        self.is_on() && self.loud()
            && (self.every_shot.load(Ordering::Relaxed) || self.clicks.fetch_add(1, Ordering::SeqCst) < CLICK_SHOTS)
    }

    /// A message to the channel, now (the caller's thread waits).
    pub fn post(&self, text: &str, png: Option<Vec<u8>>) {
        let url = self.url.lock().unwrap().clone();
        if url.is_empty() {
            return;
        }
        let who = self.who.lock().unwrap().clone();
        let files = png.map(|p| vec![("shot.png".to_string(), p)]).unwrap_or_default();
        let (kind, body) = discord::form(&clip(&format!("`{who}` {text}"), 2000), true, &files);
        let agent = ureq::AgentBuilder::new().timeout(Duration::from_secs(20)).build();
        for attempt in 0..4 {
            match agent.post(&url).set("Content-Type", &kind).set("User-Agent", "Hunt-debug (catbot, 1.0)").send_bytes(&body) {
                Ok(_) => return,
                Err(ureq::Error::Status(429, r)) => {
                    let wait = r.into_json::<Value>().ok().and_then(|j| j["retry_after"].as_f64()).unwrap_or(2.0);
                    sleep(wait + 0.2);
                }
                Err(ureq::Error::Status(..)) => return, // anything but "slow down" won't get better
                Err(_) => sleep(3.0 * (attempt + 1) as f64),
            }
        }
    }
}
