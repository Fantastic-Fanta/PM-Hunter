//! What a run keeps, in Hunt's folder (HUNT_DATA; %LOCALAPPDATA%\Hunt on
//! Windows, the app's own files on Android): config.json, calibration.json,
//! menu.json, encounters.csv, website.key, debug-run.json, finds/, debug/.

use crate::layout::Calibration;
use crate::util::{local, write_atomic};
use serde_json::{Map, Value};
use std::path::{Path, PathBuf};

#[derive(Clone, Debug)]
pub struct Store {
    pub dir: PathBuf,
}

impl Store {
    pub fn new(dir: impl Into<PathBuf>) -> Store {
        let dir = dir.into();
        let _ = std::fs::create_dir_all(&dir);
        Store { dir }
    }

    pub fn path(&self, name: &str) -> PathBuf {
        self.dir.join(name)
    }

    pub fn config(&self) -> PathBuf {
        self.path("config.json")
    }

    pub fn calibration_file(&self) -> PathBuf {
        self.path("calibration.json")
    }

    pub fn read_json(path: &Path) -> Map<String, Value> {
        std::fs::read_to_string(path).ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|v| v.as_object().cloned())
            .unwrap_or_default()
    }

    /// The saved calibration for a screen size ("2000x1250").
    pub fn calibration(&self, file: &Path, key: &str) -> Option<Calibration> {
        Store::read_json(file).get(key).and_then(Calibration::from_json)
    }

    /// Saved with the others, a screen size per line.
    pub fn save_calibration(&self, file: &Path, key: &str, cal: &Calibration) {
        let mut all = Store::read_json(file);
        all.insert(key.into(), cal.to_json());
        let lines: Vec<String> = all.iter().map(|(k, v)| format!(" {}: {}", Value::from(k.as_str()), v)).collect();
        let _ = write_atomic(file, format!("{{\n{}\n}}\n", lines.join(",\n")).as_bytes());
    }

    pub fn forget_calibrations(&self) {
        let _ = std::fs::remove_file(self.calibration_file());
        let _ = std::fs::remove_file(self.path("menu.json"));
    }

    /// A row of encounters.csv (its header first, the first time).
    pub fn log_encounter(&self, row: &[String]) {
        use std::io::Write;
        let path = self.path("encounters.csv");
        let new = !path.exists();
        let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) else { return };
        if new {
            let _ = f.write_all(b"time,species,level,shiny,tint,reskin,gradient,action\r\n");
        }
        let mut cells = vec![local("%Y-%m-%d %H:%M:%S")];
        cells.extend(row.iter().cloned());
        let line = cells.iter().map(|c| csv(c)).collect::<Vec<_>>().join(",");
        let _ = f.write_all(format!("{line}\r\n").as_bytes());
    }

    /// A picture in debug/ (the last 30 kept) or finds/: its path.
    pub fn save_picture(&self, png: &[u8], folder: &str, name: &str) -> String {
        let dir = self.path(folder);
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join(format!("{}{name}.png", local("%Y%m%d-%H%M%S-")));
        let _ = std::fs::write(&path, png);
        if folder == "debug" {
            let mut old: Vec<PathBuf> = std::fs::read_dir(&dir).into_iter().flatten().flatten()
                .map(|e| e.path()).filter(|p| p.extension().is_some_and(|e| e == "png")).collect();
            old.sort();
            let n = old.len().saturating_sub(30);
            for p in &old[..n] {
                let _ = std::fs::remove_file(p);
            }
        }
        path.to_string_lossy().into_owned()
    }

    /// A line in debug/errors.log.
    pub fn error(&self, text: &str) {
        use std::io::Write;
        let dir = self.path("debug");
        let _ = std::fs::create_dir_all(&dir);
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(dir.join("errors.log")) {
            let _ = write!(f, "\n{}\n{text}\n", local("%Y-%m-%d %H:%M:%S"));
        }
    }
}

fn csv(s: &str) -> String {
    if s.contains([',', '"', '\n', '\r']) { format!("\"{}\"", s.replace('"', "\"\"")) } else { s.to_string() }
}
