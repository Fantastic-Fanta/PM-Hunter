//! The hunt's link to the website (poopihunt.xyz): asks for the login code when
//! the hunt starts, sends what the hunter shows there, and takes the page's
//! commands. The device makes every connection: commands come back on a
//! request the server holds open (/api/device/poll).

use crate::util::{random_bytes, sleep};
use base64::Engine;
use serde_json::{json, Value};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

pub fn site() -> String {
    std::env::var("HUNT_SITE").unwrap_or_else(|_| "https://poopihunt.xyz".into()).trim_end_matches('/').to_string()
}

/// This device's secret for the website, made the first time (website.key).
pub fn key(path: &Path) -> String {
    if let Ok(k) = std::fs::read_to_string(path) {
        let k = k.trim();
        if k.len() >= 40 {
            return k.to_string();
        }
    }
    let k = new_key();
    let _ = std::fs::write(path, &k);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
    }
    k
}

/// 32 random bytes, URL-safe base64 (Python's secrets.token_urlsafe(32)).
pub fn new_key() -> String {
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(random_bytes(32))
}

#[derive(Default)]
struct Slots {
    state: Option<Value>,
    shot: Option<Vec<u8>>,
    sprite: Option<Value>,
}

pub type OnCommand = Box<dyn Fn(Value) + Send + Sync>;
pub type OnCode = Box<dyn Fn(Option<String>) + Send + Sync>;

pub struct Link {
    key: String,
    build: String,
    site: String,
    label: String,
    kind: String,
    code: Mutex<String>,
    logged_in: AtomicBool,
    stopped: AtomicBool,
    slots: Mutex<Slots>,
    wake: Condvar,
    on_command: OnCommand,
    on_code: OnCode,
}

impl Link {
    pub fn new(key: &str, build: &str, label: &str, kind: &str, on_command: OnCommand, on_code: OnCode) -> Arc<Link> {
        Arc::new(Link {
            key: key.into(),
            build: build.into(),
            site: site(),
            label: label.into(),
            kind: kind.into(),
            code: Mutex::new(String::new()),
            logged_in: AtomicBool::new(false),
            stopped: AtomicBool::new(false),
            slots: Mutex::new(Slots::default()),
            wake: Condvar::new(),
            on_command,
            on_code,
        })
    }

    pub fn start(self: &Arc<Self>) {
        let me = self.clone();
        std::thread::Builder::new().name("website".into()).spawn(move || me.connect()).expect("website thread");
        let me = self.clone();
        std::thread::Builder::new().name("website-send".into()).spawn(move || me.send()).expect("website thread");
    }

    pub fn stop(self: &Arc<Self>) {
        if self.stopped.swap(true, Ordering::SeqCst) {
            return;
        }
        self.wake.notify_all();
        let me = self.clone();
        let t = std::thread::spawn(move || {
            let _ = me.call("POST", "/api/device/stop", Some(json!({})), None, 5);
        });
        // a moment for it to get there (Hunt may be closing)
        for _ in 0..30 {
            if t.is_finished() {
                break;
            }
            sleep(0.05);
        }
    }

    pub fn site(&self) -> &str {
        &self.site
    }

    /// The code to type on the website; empty until the server gives one, and
    /// once it's spent (a code works once).
    pub fn code(&self) -> String {
        self.code.lock().unwrap().clone()
    }

    /// A browser logged in with the last code.
    pub fn logged_in(&self) -> bool {
        self.logged_in.load(Ordering::Relaxed)
    }

    fn set_code(&self, code: &str) {
        *self.code.lock().unwrap() = code.to_string();
        (self.on_code)(if code.is_empty() { None } else { Some(code.to_string()) });
    }

    pub fn send_state(&self, s: Value) {
        self.slots.lock().unwrap().state = Some(s);
        self.wake.notify_all();
    }

    pub fn send_shot(&self, jpg: Vec<u8>) {
        self.slots.lock().unwrap().shot = Some(jpg);
        self.wake.notify_all();
    }

    /// The wild Pokemon as the game draws it: {"count", "species", "png"} (base64).
    pub fn send_sprite(&self, sprite: Value) {
        self.slots.lock().unwrap().sprite = Some(sprite);
        self.wake.notify_all();
    }

    /// A new code; the old one stops working. Waits on the network.
    pub fn new_code(&self) -> Result<(), String> {
        let r = self.call("POST", "/api/device/code", Some(json!({})), None, 15)?;
        self.logged_in.store(false, Ordering::Relaxed);
        self.set_code(r["code"].as_str().unwrap_or(""));
        Ok(())
    }

    /// Logs every browser out of this device. Waits on the network.
    pub fn log_out_browsers(&self) -> Result<(), String> {
        self.call("POST", "/api/device/logout", Some(json!({})), None, 15)?;
        self.logged_in.store(false, Ordering::Relaxed);
        Ok(())
    }

    fn stopped(&self) -> bool {
        self.stopped.load(Ordering::Relaxed)
    }

    /// A code, then commands until stopped. Offline: tries again.
    fn connect(&self) {
        let mut wait = 2.0;
        while !self.stopped() && self.code().is_empty() {
            let start = json!({"label": self.label, "kind": self.kind, "build": self.build});
            match self.call("POST", "/api/device/start", Some(start), None, 15) {
                Ok(r) if r["code"].as_str().is_some() => self.set_code(r["code"].as_str().unwrap()),
                _ => {
                    sleep(wait);
                    wait = f64::min(wait * 2.0, 60.0);
                }
            }
        }
        let mut wait = 2.0;
        while !self.stopped() {
            match self.call("GET", "/api/device/poll", None, None, 40) {
                Ok(r) => {
                    for cmd in r["commands"].as_array().cloned().unwrap_or_default() {
                        if cmd["cmd"] == "login" {
                            self.logged_in.store(true, Ordering::Relaxed);
                            self.set_code(""); // spent, right or wrong
                        }
                        if !self.stopped() {
                            (self.on_command)(cmd);
                        }
                    }
                    wait = 2.0;
                }
                Err(_) => {
                    if self.stopped() {
                        break;
                    }
                    sleep(wait);
                    wait = f64::min(wait * 2.0, 30.0);
                }
            }
        }
    }

    /// The newest state, sprite and picture, as they come; older ones are skipped.
    fn send(&self) {
        let (mut last, mut sent_at): (Option<Value>, f64) = (None, 0.0);
        while !self.stopped() {
            let (state, shot, sprite) = {
                let slots = self.slots.lock().unwrap();
                let (mut slots, _) = self.wake.wait_timeout_while(slots, Duration::from_secs(5), |s| {
                    s.state.is_none() && s.shot.is_none() && s.sprite.is_none() && !self.stopped()
                }).unwrap();
                (slots.state.take(), slots.shot.take(), slots.sprite.take())
            };
            // the last state again every 20 s: a restarted server knows it again
            let state = state.or_else(|| last.clone().filter(|_| crate::util::now() - sent_at > 20.0));
            let r = (|| -> Result<(), String> {
                if let Some(s) = sprite {
                    self.call("POST", "/api/device/sprite", Some(s), None, 15)?;
                }
                if let Some(jpg) = shot {
                    self.call("POST", "/api/device/shot", None, Some(jpg), 15)?;
                }
                if let Some(s) = state {
                    self.call("POST", "/api/device/state", Some(s.clone()), None, 15)?;
                    last = Some(s);
                    sent_at = crate::util::now();
                }
                Ok(())
            })();
            if r.is_err() {
                sleep(2.0); // offline: the next state replaces this one
            }
        }
    }

    fn call(&self, method: &str, path: &str, data: Option<Value>, jpg: Option<Vec<u8>>, timeout: u64) -> Result<Value, String> {
        let agent = ureq::AgentBuilder::new().timeout(Duration::from_secs(timeout)).build();
        let req = agent.request(method, &format!("{}{path}", self.site))
            .set("Authorization", &format!("Bearer {}", self.key))
            .set("User-Agent", &format!("Hunt ({}, {})", self.kind, self.build));
        let resp = if let Some(jpg) = jpg {
            req.set("Content-Type", "image/jpeg").send_bytes(&jpg)
        } else if let Some(d) = data {
            req.set("Content-Type", "application/json").send_string(&d.to_string())
        } else {
            req.call()
        };
        let text = resp.map_err(|e| e.to_string())?.into_string().map_err(|e| e.to_string())?;
        if text.trim().is_empty() {
            Ok(json!({}))
        } else {
            serde_json::from_str(&text).map_err(|e| e.to_string())
        }
    }
}
