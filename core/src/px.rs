//! Screenshots and what's measured on them: boxes, points, the share of an
//! area's pixels that pass a test, its median color, and the blobs of a mask.

use image::RgbImage;

/// A screenshot, RGB, row by row.
pub type Frame = RgbImage;

/// A box on a screenshot (screen px): left, top, right, bottom.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub struct IBox {
    pub l: i32,
    pub t: i32,
    pub r: i32,
    pub b: i32,
}

impl IBox {
    pub const fn new(l: i32, t: i32, r: i32, b: i32) -> IBox {
        IBox { l, t, r, b }
    }
    pub fn w(&self) -> i32 {
        self.r - self.l
    }
    pub fn h(&self) -> i32 {
        self.b - self.t
    }
    pub fn overlaps(&self, o: &IBox) -> bool {
        self.l < o.r && o.l < self.r && self.t < o.b && o.t < self.b
    }
    pub fn center(&self) -> Pt {
        Pt::new((self.l + self.r) as f64 / 2.0, (self.t + self.b) as f64 / 2.0)
    }
}

/// A point on a screenshot (screen px).
#[derive(Clone, Copy, Debug, PartialEq, Default)]
pub struct Pt {
    pub x: f64,
    pub y: f64,
}

impl Pt {
    pub const fn new(x: f64, y: f64) -> Pt {
        Pt { x, y }
    }
}

pub type Rgb = [u8; 3];

/// The part of b inside f: (x0, y0, x1, y1), possibly empty.
pub fn clip(f: &Frame, b: IBox) -> (usize, usize, usize, usize) {
    let (w, h) = (f.width() as i32, f.height() as i32);
    let x0 = b.l.clamp(0, w);
    let y0 = b.t.clamp(0, h);
    let x1 = b.r.clamp(x0, w);
    let y1 = b.b.clamp(y0, h);
    (x0 as usize, y0 as usize, x1 as usize, y1 as usize)
}

pub fn whole(f: &Frame) -> IBox {
    IBox::new(0, 0, f.width() as i32, f.height() as i32)
}

#[inline]
pub fn at(f: &Frame, x: usize, y: usize) -> Rgb {
    let i = (y * f.width() as usize + x) * 3;
    let p = f.as_raw();
    [p[i], p[i + 1], p[i + 2]]
}

/// Calls each with every pixel inside b.
#[inline]
pub fn each(f: &Frame, b: IBox, mut each: impl FnMut(Rgb)) {
    let (x0, y0, x1, y1) = clip(f, b);
    let w = f.width() as usize;
    let p = f.as_raw();
    for y in y0..y1 {
        let row = &p[(y * w + x0) * 3..(y * w + x1) * 3];
        for c in row.chunks_exact(3) {
            each([c[0], c[1], c[2]]);
        }
    }
}

/// The pixels inside b: how many.
pub fn count(f: &Frame, b: IBox) -> usize {
    let (x0, y0, x1, y1) = clip(f, b);
    (x1 - x0) * (y1 - y0)
}

/// The share of the pixels inside b that pass test (0 for none).
pub fn share(f: &Frame, b: IBox, test: impl Fn(Rgb) -> bool) -> f64 {
    let (mut n, mut hit) = (0usize, 0usize);
    each(f, b, |c| {
        n += 1;
        hit += test(c) as usize;
    });
    if n == 0 { 0.0 } else { hit as f64 / n as f64 }
}

pub fn mask(f: &Frame, b: IBox, test: impl Fn(Rgb) -> bool) -> Mask {
    let (x0, y0, x1, y1) = clip(f, b);
    let (w, h) = (x1 - x0, y1 - y0);
    let mut on = Vec::with_capacity(w * h);
    each(f, b, |c| on.push(test(c)));
    Mask { x0: x0 as i32, y0: y0 as i32, w, h, on }
}

/// Median color of the pixels inside b that pass test, as numpy's median has it
/// (the middle two averaged, for an even count). None when no pixel passes.
pub fn median(f: &Frame, b: IBox, test: impl Fn(Rgb) -> bool) -> Option<[f64; 3]> {
    let mut hist = [[0u32; 256]; 3];
    let mut n = 0u32;
    each(f, b, |c| {
        if test(c) {
            for k in 0..3 {
                hist[k][c[k] as usize] += 1;
            }
            n += 1;
        }
    });
    if n == 0 {
        return None;
    }
    let nth = |h: &[u32; 256], k: u32| -> f64 {
        let mut seen = 0;
        for (v, &c) in h.iter().enumerate() {
            seen += c;
            if seen > k {
                return v as f64;
            }
        }
        255.0
    };
    let mut out = [0.0; 3];
    for (k, h) in hist.iter().enumerate() {
        out[k] = if n % 2 == 1 { nth(h, n / 2) } else { (nth(h, n / 2 - 1) + nth(h, n / 2)) / 2.0 };
    }
    Some(out)
}

/// Mean color of the pixels inside b.
pub fn mean(f: &Frame, b: IBox) -> Option<[f64; 3]> {
    let mut sum = [0u64; 3];
    let mut n = 0u64;
    each(f, b, |c| {
        for k in 0..3 {
            sum[k] += c[k] as u64;
        }
        n += 1;
    });
    (n > 0).then(|| sum.map(|s| s as f64 / n as f64))
}

#[inline]
pub fn near(c: Rgb, want: [i32; 3], tol: i32) -> bool {
    (c[0] as i32 - want[0]).abs() < tol && (c[1] as i32 - want[1]).abs() < tol && (c[2] as i32 - want[2]).abs() < tol
}

#[inline]
pub fn hi(c: Rgb) -> u8 {
    c[0].max(c[1]).max(c[2])
}

#[inline]
pub fn lo(c: Rgb) -> u8 {
    c[0].min(c[1]).min(c[2])
}

/// A True / False mask over a box of a screenshot.
pub struct Mask {
    pub x0: i32,
    pub y0: i32,
    pub w: usize,
    pub h: usize,
    pub on: Vec<bool>,
}

/// A blob of a mask: its screen box and how many pixels it has.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Blob {
    pub b: IBox,
    pub size: usize,
}

impl Mask {
    /// The blobs (pixels touching sideways or up and down), in the order a
    /// row by row scan meets them. Rows are cut into runs of on pixels, and
    /// runs that overlap in neighboring rows are joined.
    pub fn blobs(&self) -> Vec<Blob> {
        // runs: (row, start, end)
        let mut runs: Vec<(usize, usize, usize)> = Vec::new();
        let mut row_first = vec![0usize; self.h + 1]; // index of each row's first run
        for y in 0..self.h {
            row_first[y] = runs.len();
            let row = &self.on[y * self.w..(y + 1) * self.w];
            let mut x = 0;
            while x < self.w {
                if row[x] {
                    let start = x;
                    while x < self.w && row[x] {
                        x += 1;
                    }
                    runs.push((y, start, x));
                } else {
                    x += 1;
                }
            }
        }
        row_first[self.h] = runs.len();
        if runs.is_empty() {
            return Vec::new();
        }
        // union-find, each blob's root its first run
        let mut parent: Vec<usize> = (0..runs.len()).collect();
        fn find(p: &mut [usize], mut i: usize) -> usize {
            while p[i] != i {
                p[i] = p[p[i]];
                i = p[i];
            }
            i
        }
        for y in 1..self.h {
            let (mut a, a_end) = (row_first[y - 1], row_first[y]);
            let (mut b, b_end) = (row_first[y], row_first[y + 1]);
            while a < a_end && b < b_end {
                let (_, s0, e0) = runs[a];
                let (_, s1, e1) = runs[b];
                if s0 < e1 && s1 < e0 {
                    let (ra, rb) = (find(&mut parent, a), find(&mut parent, b));
                    if ra != rb {
                        let (lo, hi) = (ra.min(rb), ra.max(rb));
                        parent[hi] = lo;
                    }
                }
                if e0 < e1 { a += 1 } else { b += 1 }
            }
        }
        let mut slot = vec![usize::MAX; runs.len()];
        let mut out: Vec<Blob> = Vec::new();
        for i in 0..runs.len() {
            let r = find(&mut parent, i);
            let (y, s, e) = runs[i];
            let (y, s, e) = (y as i32 + self.y0, s as i32 + self.x0, e as i32 + self.x0);
            if slot[r] == usize::MAX {
                slot[r] = out.len();
                out.push(Blob { b: IBox::new(s, y, e, y + 1), size: 0 });
            }
            let blob = &mut out[slot[r]];
            blob.b.l = blob.b.l.min(s);
            blob.b.r = blob.b.r.max(e);
            blob.b.t = blob.b.t.min(y);
            blob.b.b = blob.b.b.max(y + 1);
            blob.size += (e - s) as usize;
        }
        out
    }
}

/// Least squares y = k x + d.
pub fn line(x: &[f64], y: &[f64]) -> (f64, f64) {
    let n = x.len() as f64;
    let mx = x.iter().sum::<f64>() / n;
    let my = y.iter().sum::<f64>() / n;
    let sxy: f64 = x.iter().zip(y).map(|(a, b)| (a - mx) * (b - my)).sum();
    let sxx: f64 = x.iter().map(|a| (a - mx) * (a - mx)).sum();
    let k = sxy / sxx;
    (k, my - k * mx)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blobs_join_runs_and_keep_scan_order() {
        // two blobs: a U shape (joined at the bottom) and a dot
        let rows = ["x.x..", "x.x.x", "xxx.."];
        let on: Vec<bool> = rows.iter().flat_map(|r| r.chars().map(|c| c == 'x')).collect();
        let m = Mask { x0: 10, y0: 20, w: 5, h: 3, on };
        let b = m.blobs();
        assert_eq!(b.len(), 2);
        assert_eq!(b[0], Blob { b: IBox::new(10, 20, 13, 23), size: 7 });
        assert_eq!(b[1], Blob { b: IBox::new(14, 21, 15, 22), size: 1 });
    }
}
