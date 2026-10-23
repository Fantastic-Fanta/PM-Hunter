//! Fitting the layout to a screen. Another window shape can stretch the game
//! differently across than down, and Windows shows colors a little differently
//! than a Mac. So the Fight/Switch/Bag/Run buttons and the enemy's box are found
//! on screen and the layout is fitted to them, once per screen size, and again
//! when the battle can't be read.

use crate::layout::{self, Calibration, Layout};
use crate::px::{self, line, mask, median, near, Frame, IBox};
use crate::vision::menu_ready_with;

/// Screen box of the patch of color around where lay puts ref, if it's about
/// the expected size. The search reaches far: the game may be stretched a lot
/// more down than across.
pub fn find_block(f: &Frame, lay: &Layout, r: IBox, color: [i32; 3]) -> Option<IBox> {
    let (cx, cy) = ((r.l + r.r) as f64 / 2.0, (r.t + r.b) as f64 / 2.0);
    let pad_x = r.w() as f64 / 2.0 + 0.3 * (cx - layout::MUTE.l as f64).abs();
    let pad_y = r.h() as f64 / 2.0 + 0.3 * (cy - layout::MUTE.t as f64).abs();
    let area = lay.boxf(r.l as f64 - pad_x, r.t as f64 - pad_y, r.r as f64 + pad_x, r.b as f64 + pad_y);
    if px::count(f, area) == 0 {
        return None;
    }
    for tol in [20, 35] {
        let found = mask(f, area, |c| near(c, color, tol)).blobs();
        // the biggest (the first of those as big)
        let Some(best) = found.iter().fold(None::<&px::Blob>, |b, x| if b.is_some_and(|b| b.size >= x.size) { b } else { Some(x) })
        else { continue };
        let (h, w) = (best.b.h() as f64, best.b.w() as f64);
        let (kw, kh) = (w / (r.w() as f64 * lay.s), h / (r.h() as f64 * lay.s));
        if 0.6 < kw && kw < 1.6 && 0.6 < kh && kh < 1.6 && best.size as f64 > 0.5 * w * h {
            return Some(best.b);
        }
    }
    None
}

/// got = k * ref + d through the edges found, (k, d, worst miss). An edge
/// that's off (a button's patch measured too wide: its icon not loaded, a white
/// square; two buttons run together) is left out, the worst first, while more
/// than 5 are left.
pub fn fit_edges(r: &[f64], got: &[f64]) -> (f64, f64, f64) {
    let mut keep = vec![true; r.len()];
    loop {
        let (xs, ys): (Vec<f64>, Vec<f64>) =
            r.iter().zip(got).zip(&keep).filter(|(_, k)| **k).map(|((a, b), _)| (*a, *b)).unzip();
        let (k, d) = line(&xs, &ys);
        let miss: Vec<f64> = (0..r.len()).map(|i| if keep[i] { (k * r[i] + d - got[i]).abs() } else { 0.0 }).collect();
        let (worst, max) = miss.iter().enumerate().fold((0, f64::MIN), |b, (i, m)| if *m > b.1 { (i, *m) } else { b });
        if max <= 12.0 || keep.iter().filter(|k| **k).count() <= 5 {
            return (k, d, max);
        }
        keep[worst] = false;
    }
}

/// Fits the layout to the buttons and the enemy's box on this screen.
/// The calibration (its miss in screen px), or None if too little of the
/// battle screen was found (the menu isn't up, say).
pub fn calibrate(f: &Frame, mute: IBox) -> Option<Calibration> {
    let flat = Layout::flat(mute);
    let (mut refs, mut got) = (Vec::new(), Vec::new());
    let mut cal = Calibration::default();
    for (name, r) in layout::ANCHORS {
        let color = layout::button_color(name);
        if let Some(b) = find_block(f, &flat, r, color) {
            refs.push(r);
            got.push(b);
            let m = median(f, b, |c| near(c, color, 35));
            cal.colors.insert(name.into(), m.map(|m| m.map(|v| v as i32)).unwrap_or(color));
        }
    }
    if refs.len() - cal.colors.contains_key("box") as usize >= 2 {
        refs.push(layout::MUTE);
        got.push(mute);
    } else {
        return None;
    }
    let s = flat.s;
    let rx: Vec<f64> = refs.iter().map(|b| b.l).chain(refs.iter().map(|b| b.r)).map(|v| (v - layout::MUTE.l) as f64).collect();
    let gx: Vec<f64> = got.iter().map(|b| b.l).chain(got.iter().map(|b| b.r)).map(|v| (v - mute.l) as f64 / s).collect();
    let ry: Vec<f64> = refs.iter().map(|b| b.t).chain(refs.iter().map(|b| b.b)).map(|v| (v - layout::MUTE.t) as f64).collect();
    let gy: Vec<f64> = got.iter().map(|b| b.t).chain(got.iter().map(|b| b.b)).map(|v| (v - mute.t) as f64 / s).collect();
    let (kx, dx, miss_x) = fit_edges(&rx, &gx);
    let (ky, dy, miss_y) = fit_edges(&ry, &gy);
    let miss = miss_x.max(miss_y);
    if !(0.6 < kx && kx < 1.6 && 0.6 < ky && ky < 1.6) || miss > 12.0 {
        return None; // the anchors don't agree on a layout
    }
    cal.fit = [kx, ky, dx, dy];
    cal.miss = miss * s;
    menu_ready_with(f, &Layout::new(mute, &cal), &cal).then_some(cal)
}
