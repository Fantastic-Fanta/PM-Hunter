//! Small things: time, randomness, files.

use std::path::Path;
use std::sync::LazyLock;
use std::time::{Duration, Instant};

static START: LazyLock<Instant> = LazyLock::new(Instant::now);

/// Seconds, steadily counting: from a million when the program starts, so a time
/// of 0 ("never": not saved yet, nothing clicked yet) is always long ago.
pub fn now() -> f64 {
    1_000_000.0 + START.elapsed().as_secs_f64()
}

pub fn sleep(seconds: f64) {
    if seconds > 0.0 {
        std::thread::sleep(Duration::from_secs_f64(seconds));
    }
}

pub fn random_bytes(n: usize) -> Vec<u8> {
    let mut b = vec![0u8; n];
    if getrandom::getrandom(&mut b).is_err() {
        // (never seen; the clock's nanoseconds rather than nothing)
        let t = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos();
        for (i, x) in b.iter_mut().enumerate() {
            *x = (t >> ((i % 16) * 8)) as u8 ^ (i as u8).wrapping_mul(151);
        }
    }
    b
}

pub fn random_hex(n: usize) -> String {
    random_bytes(n).iter().map(|b| format!("{b:02x}")).collect()
}

/// Local time, as strftime has it ("%Y-%m-%d %H:%M:%S").
pub fn local(format: &str) -> String {
    chrono::Local::now().format(format).to_string()
}

/// Writes a file whole, through a new one renamed over it.
pub fn write_atomic(path: &Path, data: &[u8]) -> std::io::Result<()> {
    let tmp = path.with_extension("new");
    std::fs::write(&tmp, data)?;
    std::fs::rename(&tmp, path)
}

/// The first letter lower case: "Throwing a ..." -> "throwing a ...".
pub fn lower_first(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        Some(f) => f.to_lowercase().collect::<String>() + c.as_str(),
        None => String::new(),
    }
}

/// At most n characters.
pub fn clip(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

#[cfg(test)]
mod tests {
    #[test]
    fn never_is_long_ago() {
        // a save "due since never" (0) is due at once, the first minutes too
        assert!(super::now() - 0.0 > 125.0);
    }
}
