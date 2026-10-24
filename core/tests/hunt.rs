//! The hunt itself on a pretend screen: a sample battle that ends when Run is
//! clicked, the enemy's box "read" as the samples' text.

use hunt_core::device::{Device, Sound, Traits};
use hunt_core::layout::{self, Layout};
use hunt_core::out::{DebugChannel, Sink};
use hunt_core::px::{Frame, Pt};
use hunt_core::{picture, Config, Options, Store, TextHit};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

struct Fake {
    battle: Frame,
    calm: Frame,
    enemy: (u32, Vec<TextHit>), // the enemy box's width, what's read in it
    log: Mutex<Vec<String>>,
    in_battle: Mutex<bool>,
}

impl Device for Fake {
    fn traits(&self) -> Traits {
        Traits { phone: false, kind: "test", seen_after: 0.0, poll: 0.05, ocr_rows: false, toggle_key: "Ctrl+Shift+H" }
    }
    fn grab(&self) -> Frame {
        std::thread::sleep(Duration::from_millis(5));
        if *self.in_battle.lock().unwrap() { self.battle.clone() } else { self.calm.clone() }
    }
    fn click(&self, at: Pt, _right: bool) {
        self.log.lock().unwrap().push(format!("click {:.0} {:.0}", at.x, at.y));
        let run = Layout::flat(layout::MUTE).pt(layout::RUN);
        if (at.x - run.x).abs() < 5.0 && (at.y - run.y).abs() < 5.0 {
            *self.in_battle.lock().unwrap() = false;
        }
    }
    fn type_text(&self, _text: &str) -> bool {
        true
    }
    fn roblox_in_front(&self) -> bool {
        true
    }
    fn focus_roblox(&self) {}
    fn ocr(&self, img: &Frame) -> Vec<TextHit> {
        if img.width() == self.enemy.0 { self.enemy.1.clone() } else { Vec::new() }
    }
    fn sound(&self, s: Sound) {
        self.log.lock().unwrap().push(format!("sound {s:?}"));
    }
    fn label(&self) -> String {
        "test".into()
    }
    fn system(&self) -> String {
        "test".into()
    }
}

struct Lines(Mutex<Vec<String>>);

impl Sink for Lines {
    fn line(&self, text: &str) {
        self.0.lock().unwrap().push(text.to_string());
    }
}

fn hunt(enemy: Vec<TextHit>, config: &str, done: impl Fn(&[String], &[String]) -> bool) -> (Vec<String>, Vec<String>) {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
    let battle = picture::open(&root.join("samples/quaxly-shiny.png")).unwrap();
    let calm = Frame::from_pixel(battle.width(), battle.height(), image::Rgb([20, 30, 40]));
    let box_w = Layout::flat(layout::MUTE).boxed(layout::BOX).w() as u32;
    let dev = Arc::new(Fake { battle, calm, enemy: (box_w, enemy), log: Mutex::new(Vec::new()), in_battle: Mutex::new(false) });
    let dir = std::env::temp_dir().join(format!("hunt-test-{}", hunt_core::util::random_hex(4)));
    let store = Store::new(&dir);
    let lines = Arc::new(Lines(Mutex::new(Vec::new())));
    let cfg = Config::with_rules(config).unwrap();
    let (control, hunter) = hunt_core::start(dev.clone(), lines.clone(), DebugChannel::off(), cfg, store,
                                             Options { build: "test".into(), ..Default::default() });
    let thread = std::thread::spawn(move || hunter.run());
    std::thread::sleep(Duration::from_millis(300));
    *dev.in_battle.lock().unwrap() = true; // an encounter comes in
    let t0 = Instant::now();
    while t0.elapsed() < Duration::from_secs(20) && !done(&lines.0.lock().unwrap(), &dev.log.lock().unwrap()) {
        std::thread::sleep(Duration::from_millis(50));
    }
    std::thread::sleep(Duration::from_millis(300));
    control.stop();
    thread.join().unwrap();
    let _ = std::fs::remove_dir_all(&dir);
    let out = (lines.0.lock().unwrap().clone(), dev.log.lock().unwrap().clone());
    out
}

#[test]
fn runs_from_an_unwanted_one() {
    let (lines, did) = hunt(vec![TextHit::new("Quaxly", 0.2, 0.15), TextHit::new("Lv. 7", 0.7, 0.15)],
                            r#"{"targets": [{"species": "Lapras"}]}"#,
                            |lines, _| lines.iter().filter(|l| l.contains("waiting for an encounter")).count() >= 2);
    let said = lines.join("\n");
    assert!(said.contains("shiny Quaxly Lv7"), "{said}");
    assert!(said.contains(" run"), "{said}");
    let run = Layout::flat(layout::MUTE).pt(layout::RUN);
    assert!(did.contains(&format!("click {:.0} {:.0}", run.x, run.y)), "{did:?}\n{said}");
    assert!(said.contains("stopped."), "{said}");
}

#[test]
fn holds_a_wanted_one() {
    let (lines, did) = hunt(vec![TextHit::new("Quaxly", 0.2, 0.15), TextHit::new("Lv. 7", 0.7, 0.15)],
                            r#"{"targets": [{"species": "Quaxly", "shiny": true}]}"#,
                            |_, did| did.iter().filter(|d| d.starts_with("click")).count() >= 2);
    let said = lines.join("\n");
    assert!(said.contains("WANTED!"), "{said}");
    assert!(did.iter().any(|d| d == "sound Found"), "{did:?}");
    let run = Layout::flat(layout::MUTE).pt(layout::RUN);
    assert!(!did.contains(&format!("click {:.0} {:.0}", run.x, run.y)), "it ran from it: {did:?}");
}
