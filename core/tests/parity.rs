//! The same answers as pokehunt.py (the Python hunter this was ported from, as of 51c9a8a) on
//! every sample in samples/: parity.json is what it saw, by its colors (the
//! text recognition is each platform's own).

use hunt_core::calibrate::calibrate;
use hunt_core::layout::{self, Layout};
use hunt_core::picture;
use hunt_core::px::{self, mask, IBox};
use hunt_core::vision::{self, box_visible, chat_showing, find_mute, menu_ready, red};
use serde_json::Value;
use std::path::PathBuf;

fn samples() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../samples")
}

fn close(a: f64, b: &Value, what: &str) {
    let b = b.as_f64().unwrap_or_else(|| panic!("{what}: {b}"));
    assert!((a - b).abs() < 1e-6, "{what}: {a} here, {b} in Python");
}

fn pt(p: Option<hunt_core::Pt>) -> Value {
    p.map(|p| serde_json::json!([(p.x * 1000.0).round() / 1000.0, (p.y * 1000.0).round() / 1000.0])).unwrap_or(Value::Null)
}

#[test]
fn every_sample_like_python() {
    let want: serde_json::Map<String, Value> =
        serde_json::from_str(include_str!("parity.json")).expect("parity.json");
    assert!(want.len() >= 14);
    for (name, w) in &want {
        let f = picture::open(&samples().join(name)).unwrap();
        let at = |what: &str| format!("{name}: {what}");
        assert_eq!(serde_json::json!([f.width(), f.height()]), w["size"], "{}", at("size"));
        assert_eq!(mask(&f, px::whole(&f), red).blobs().len() as u64, w["red_blobs"].as_u64().unwrap(), "{}", at("red blobs"));
        let mute = find_mute(&f, None);
        let shown = mute.map(|m| serde_json::json!([m.l, m.t, m.r, m.b])).unwrap_or(Value::Null);
        assert_eq!(shown, w["mute"], "{}", at("Mute"));
        assert_eq!(pt(vision::chat_button(&f)), w["chat_button"], "{}", at("chat button"));
        assert_eq!(vision::chat_open(&f).map(Value::Bool).unwrap_or(Value::Null), w["chat_open"], "{}", at("chat open"));
        let tab = vision::find_menu_tab(&f, &[]);
        assert_eq!(pt(tab), w["menu_tab"], "{}", at("MENU tab"));
        if let Some(tab) = tab {
            let look = vision::tab_look(&f, tab);
            for k in 0..3 {
                close(look[k], &w["tab_look"][k], &at("MENU tab's look"));
            }
        }
        assert_eq!(pt(vision::find_use_button(&f, 0.0)), w["use_button"], "{}", at("Use button"));
        let Some(mute) = mute else { continue };
        let flat = Layout::flat(mute);
        assert_eq!(Value::Bool(menu_ready(&f, &flat)), w["flat"]["menu_ready"], "{}", at("menu, flat"));
        assert_eq!(Value::Bool(box_visible(&f, &flat)), w["flat"]["box_visible"], "{}", at("box, flat"));
        assert_eq!(Value::Bool(chat_showing(&f, &flat)), w["flat"]["chat_showing"], "{}", at("chat, flat"));
        let cal = calibrate(&f, mute);
        assert_eq!(cal.is_some(), !w["cal"].is_null(), "{}", at("calibrates"));
        let Some(cal) = cal else { continue };
        for k in 0..4 {
            close(cal.fit[k], &w["cal"]["fit"][k], &at("fit"));
        }
        close(cal.miss, &w["cal"]["miss"], &at("miss"));
        let colors: serde_json::Map<String, Value> = cal.colors.iter().map(|(k, v)| (k.clone(), serde_json::json!(v))).collect();
        assert_eq!(Value::Object(colors), w["cal"]["colors"], "{}", at("colors"));
        let lay = Layout::new(mute, &cal);
        let fitted = &w["fitted"];
        assert_eq!(Value::Bool(menu_ready(&f, &lay)), fitted["menu_ready"], "{}", at("menu"));
        assert_eq!(Value::Bool(box_visible(&f, &lay)), fitted["box_visible"], "{}", at("box"));
        assert_eq!(Value::Bool(chat_showing(&f, &lay)), fitted["chat_showing"], "{}", at("chat"));
        let (s, t, o) = vision::icons(&f, &lay);
        assert_eq!(serde_json::json!([s, t, o]), fitted["icons"], "{}", at("icons"));
        let run = lay.pt(layout::RUN);
        close((run.x * 1000.0).round() / 1000.0, &fitted["run"][0], &at("Run")); // (to 3 places)
        close((run.y * 1000.0).round() / 1000.0, &fitted["run"][1], &at("Run"));
        let b: IBox = lay.boxed(layout::BOX);
        assert_eq!(serde_json::json!([b.l, b.t, b.r, b.b]), fitted["box"], "{}", at("the enemy's box"));
        let sprite = vision::sprite(&f, &lay);
        let sum: u64 = sprite.as_raw().iter().map(|v| *v as u64).sum();
        assert_eq!(sum, w["sprite_sum"].as_u64().unwrap(), "{}", at("sprite"));
    }
}
