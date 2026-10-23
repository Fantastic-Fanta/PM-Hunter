//! Pictures out of screenshots: cut, scaled, as PNG or JPEG, and with what the
//! hunter reads or clicks drawn on them.

use crate::layout::{self, Layout};
use crate::px::{clip, Frame, IBox, Pt};
use image::imageops::{self, FilterType};
use image::{codecs::jpeg::JpegEncoder, ImageEncoder, Rgb};

/// The part of f inside b.
pub fn crop(f: &Frame, b: IBox) -> Frame {
    let (x0, y0, x1, y1) = clip(f, b);
    imageops::crop_imm(f, x0 as u32, y0 as u32, (x1 - x0) as u32, (y1 - y0) as u32).to_image()
}

/// Scaled to w x h, smoothly (Lanczos, as PIL's LANCZOS).
pub fn resize(f: &Frame, w: u32, h: u32) -> Frame {
    if f.width() == w && f.height() == h {
        return f.clone();
    }
    imageops::resize(f, w.max(1), h.max(1), FilterType::Lanczos3)
}

/// At most max_w wide (its shape kept).
pub fn at_most(f: Frame, max_w: u32) -> Frame {
    if f.width() <= max_w || f.width() == 0 {
        return f;
    }
    let h = (f.height() as u64 * max_w as u64 / f.width() as u64) as u32;
    resize(&f, max_w, h)
}

/// At most max x max, its shape kept (PIL's thumbnail).
pub fn thumbnail(f: Frame, max: u32) -> Frame {
    let (w, h) = (f.width(), f.height());
    if w <= max && h <= max {
        return f;
    }
    let k = (max as f64 / w as f64).min(max as f64 / h as f64);
    resize(&f, ((w as f64 * k).round() as u32).max(1), ((h as f64 * k).round() as u32).max(1))
}

pub fn png(f: &Frame) -> Vec<u8> {
    let mut out = Vec::new();
    let _ = image::codecs::png::PngEncoder::new(&mut out).write_image(f.as_raw(), f.width(), f.height(),
                                                                       image::ExtendedColorType::Rgb8);
    out
}

pub fn jpg(f: &Frame, quality: u8) -> Vec<u8> {
    let mut out = Vec::new();
    let _ = JpegEncoder::new_with_quality(&mut out, quality).write_image(f.as_raw(), f.width(), f.height(),
                                                                          image::ExtendedColorType::Rgb8);
    out
}

pub fn decode(bytes: &[u8]) -> Option<Frame> {
    image::load_from_memory(bytes).ok().map(|i| i.to_rgb8())
}

pub fn open(path: &std::path::Path) -> Result<Frame, String> {
    image::open(path).map(|i| i.to_rgb8()).map_err(|e| format!("{}: {e}", path.display()))
}

/// The game, cut out of the screenshot, at most 1200 px wide.
pub fn game_shot(f: &Frame, lay: &Layout) -> Frame {
    at_most(crop(f, lay.boxed(layout::PANEL)), 1200)
}

// ---------------------------------------------------------------- drawing

const PINK: Rgb<u8> = Rgb([255, 0, 255]);
const GREEN: Rgb<u8> = Rgb([0, 255, 0]);

fn dot(f: &mut Frame, x: i64, y: i64, width: i64, c: Rgb<u8>) {
    let (w, h) = (f.width() as i64, f.height() as i64);
    let r0 = -(width - 1) / 2;
    for dy in r0..r0 + width {
        for dx in r0..r0 + width {
            let (px, py) = (x + dx, y + dy);
            if px >= 0 && py >= 0 && px < w && py < h {
                f.put_pixel(px as u32, py as u32, c);
            }
        }
    }
}

pub fn line(f: &mut Frame, a: Pt, b: Pt, width: i64, c: Rgb<u8>) {
    let n = ((b.x - a.x).abs().max((b.y - a.y).abs()).ceil() as i64).max(1);
    for i in 0..=n {
        let t = i as f64 / n as f64;
        dot(f, (a.x + (b.x - a.x) * t).round() as i64, (a.y + (b.y - a.y) * t).round() as i64, width, c);
    }
}

pub fn rect(f: &mut Frame, b: IBox, width: i64, c: Rgb<u8>) {
    let (l, t, r, bt) = (b.l as f64, b.t as f64, b.r as f64, b.b as f64);
    for (p, q) in [((l, t), (r, t)), ((r, t), (r, bt)), ((r, bt), (l, bt)), ((l, bt), (l, t))] {
        line(f, Pt::new(p.0, p.1), Pt::new(q.0, q.1), width, c);
    }
}

pub fn circle(f: &mut Frame, at: Pt, radius: f64, width: i64, c: Rgb<u8>) {
    let n = ((radius * 8.0) as i64).max(16);
    for i in 0..n {
        let a = i as f64 / n as f64 * std::f64::consts::TAU;
        dot(f, (at.x + radius * a.cos()).round() as i64, (at.y + radius * a.sin()).round() as i64, width, c);
    }
}

/// The screenshot with every area the hunter reads or clicks drawn on it.
pub fn draw_layout(f: &Frame, lay: &Layout) -> Frame {
    let mut out = f.clone();
    let mut boxes = vec![layout::BOX, layout::ICONS, layout::MSG, layout::PLAYER_BOX, layout::CHAT_MARGIN, layout::PANEL];
    boxes.extend(layout::BUTTONS.iter().map(|b| b.1));
    for b in boxes {
        rect(&mut out, lay.boxed(b), 3, PINK);
    }
    for y in [layout::HP_BAR_END, layout::BOX_END] {
        line(&mut out, lay.xy(layout::BOX.l as f64, y as f64), lay.xy(layout::BOX.r as f64, y as f64), 3, PINK);
    }
    let chat = crate::vision::chat_button(f).unwrap_or(lay.pt(layout::CHAT_BUTTON));
    for p in [layout::RUN, layout::BAG, layout::FIGHT, layout::IDLE_SPOT, layout::AFK_SPOT].map(|p| lay.pt(p)).into_iter().chain([chat]) {
        circle(&mut out, p, 10.0, 3, GREEN);
    }
    out
}

/// The screen around a click, the spot marked (to see where clicks land); a
/// swipe's end (to) marked too.
pub fn click_closeup(f: &Frame, at: Pt, to: Option<Pt>) -> Frame {
    let (w, h) = (f.width() / 3, f.height() / 3);
    let x0 = (at.x - w as f64 / 2.0).clamp(0.0, (f.width() - w) as f64) as i32;
    let y0 = (at.y - h as f64 / 2.0).clamp(0.0, (f.height() - h) as f64) as i32;
    let mut out = crop(f, IBox::new(x0, y0, x0 + w as i32, y0 + h as i32));
    let c = Pt::new(at.x - x0 as f64, at.y - y0 as f64);
    circle(&mut out, c, 14.0, 4, PINK);
    line(&mut out, Pt::new(c.x - 26.0, c.y), Pt::new(c.x + 26.0, c.y), 2, PINK);
    line(&mut out, Pt::new(c.x, c.y - 26.0), Pt::new(c.x, c.y + 26.0), 2, PINK);
    if let Some(to) = to {
        let t = Pt::new(to.x - x0 as f64, to.y - y0 as f64);
        line(&mut out, c, t, 3, PINK);
        circle(&mut out, t, 8.0, 3, PINK);
    }
    out
}
