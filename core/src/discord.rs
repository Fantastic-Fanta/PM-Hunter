//! Discord webhooks: posts with pictures, one at a time and in order, in the
//! background; failures are said, not fatal. Slows down when Discord says so
//! (about 30 messages a minute per webhook).

use crate::util::{random_hex, sleep};
use serde_json::json;
use std::sync::mpsc::{channel, Sender};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub struct Webhook {
    url: String,
    quiet: bool,
    queue: Mutex<Option<Sender<Post>>>,
    /// Where a failure is said.
    pub on_error: Arc<dyn Fn(&str) + Send + Sync>,
}

struct Post {
    text: String,
    files: Vec<(String, Vec<u8>)>,
    done: Option<Sender<bool>>,
}

impl Webhook {
    /// quiet: @here and the like don't ping (the debug channel).
    pub fn new(url: &str, quiet: bool, on_error: Arc<dyn Fn(&str) + Send + Sync>) -> Webhook {
        Webhook { url: url.trim().to_string(), quiet, queue: Mutex::new(None), on_error }
    }

    pub fn is_on(&self) -> bool {
        !self.url.is_empty()
    }

    pub fn post(&self, text: &str, png: Option<Vec<u8>>) {
        self.send(text, png.map(|p| vec![("encounter.png".into(), p)]).unwrap_or_default(), None);
    }

    /// A message with up to 10 pictures (name, png or jpg).
    pub fn post_files(&self, text: &str, files: Vec<(String, Vec<u8>)>) {
        self.send(text, files, None);
    }

    /// Posts and waits for it: true once Discord has it.
    pub fn post_and_wait(&self, text: &str, png: Option<Vec<u8>>) -> bool {
        let (tx, rx) = channel();
        self.send(text, png.map(|p| vec![("encounter.png".into(), p)]).unwrap_or_default(), Some(tx));
        rx.recv_timeout(Duration::from_secs(120)).unwrap_or(false)
    }

    fn send(&self, text: &str, mut files: Vec<(String, Vec<u8>)>, done: Option<Sender<bool>>) {
        if !self.is_on() {
            if let Some(d) = done {
                let _ = d.send(false);
            }
            return;
        }
        files.truncate(10);
        let mut queue = self.queue.lock().unwrap();
        let tx = queue.get_or_insert_with(|| {
            let (tx, rx) = channel::<Post>();
            let (url, quiet, on_error) = (self.url.clone(), self.quiet, self.on_error.clone());
            std::thread::Builder::new()
                .name("webhook".into())
                .spawn(move || {
                    for p in rx {
                        let ok = deliver(&url, quiet, &p.text, &p.files, &*on_error);
                        if let Some(d) = p.done {
                            let _ = d.send(ok);
                        }
                    }
                })
                .expect("a thread for the webhook");
            tx
        });
        let _ = tx.send(Post { text: text.to_string(), files, done });
    }
}

/// multipart/form-data: the message as payload_json, the pictures as files[n].
pub fn form(text: &str, quiet: bool, files: &[(String, Vec<u8>)]) -> (String, Vec<u8>) {
    let boundary = random_hex(16);
    let parse: Vec<&str> = if quiet { vec![] } else { vec!["everyone", "users", "roles"] };
    let content: String = text.chars().take(2000).collect();
    let payload = json!({"content": content, "allowed_mentions": {"parse": parse}}).to_string();
    let mut body = Vec::new();
    let mut part = |disposition: &str, kind: &str, data: &[u8]| {
        body.extend_from_slice(format!("--{boundary}\r\nContent-Disposition: form-data; {disposition}\r\nContent-Type: {kind}\r\n\r\n").as_bytes());
        body.extend_from_slice(data);
        body.extend_from_slice(b"\r\n");
    };
    part("name=\"payload_json\"", "application/json", payload.as_bytes());
    for (i, (name, data)) in files.iter().enumerate() {
        let kind = if name.ends_with(".jpg") { "image/jpeg" } else { "image/png" };
        part(&format!("name=\"files[{i}]\"; filename=\"{name}\""), kind, data);
    }
    body.extend_from_slice(format!("--{boundary}--\r\n").as_bytes());
    (format!("multipart/form-data; boundary={boundary}"), body)
}

fn deliver(url: &str, quiet: bool, text: &str, files: &[(String, Vec<u8>)], on_error: &dyn Fn(&str)) -> bool {
    let (kind, body) = form(text, quiet, files);
    let agent = ureq::AgentBuilder::new().timeout(Duration::from_secs(30)).build();
    for attempt in 0..6 {
        // the network drops a request now and then
        match agent.post(url).set("Content-Type", &kind).set("User-Agent", "Hunt (catbot, 1.0)").send_bytes(&body) {
            Ok(_) => return true,
            Err(ureq::Error::Status(429, r)) => {
                // too fast: wait as long as Discord says
                let wait = r.header("Retry-After").and_then(|v| v.parse::<f64>().ok())
                    .or_else(|| r.into_json::<serde_json::Value>().ok().and_then(|j| j["retry_after"].as_f64()))
                    .unwrap_or(2.0);
                sleep(wait + 0.25);
            }
            Err(ureq::Error::Status(code, _)) if (400..500).contains(&code) => {
                on_error(&format!("  ! Discord message failed: HTTP {code}"));
                return false; // it won't get better
            }
            Err(e) => {
                if attempt == 5 {
                    on_error(&format!("  ! Discord message failed: {e}"));
                } else {
                    sleep(3.0 * (attempt + 1) as f64);
                }
            }
        }
    }
    false
}
