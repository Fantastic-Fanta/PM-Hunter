//! How long the hunter's own work takes on a 2000 x 1249 battle screenshot:
//!   cargo run --release -p hunt-core --example bench
use hunt_core::{calibrate::calibrate, layout, picture, vision};
use std::time::Instant;

fn time(n: u32, f: impl Fn()) -> f64 {
    f();
    let t = Instant::now();
    for _ in 0..n {
        f();
    }
    t.elapsed().as_secs_f64() * 1000.0 / n as f64
}

fn main() {
    let f = picture::open(std::path::Path::new(concat!(env!("CARGO_MANIFEST_DIR"), "/../samples/quaxly-shiny.png"))).unwrap();
    println!("rust: find Mute (whole screen) {:.1} ms, calibrate {:.1} ms, menu + box + chat {:.2} ms",
             time(50, || { vision::find_mute(&f, None); }),
             time(20, || { calibrate(&f, layout::MUTE); }),
             time(50, || {
                 let lay = layout::Layout::flat(layout::MUTE);
                 vision::menu_ready(&f, &lay);
                 vision::box_visible(&f, &lay);
                 vision::chat_showing(&f, &lay);
             }));
}
