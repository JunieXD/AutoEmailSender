//! Text in images, read on this computer by PaddleOCR's PP-OCRv6 small text recognition model,
//! which the app carries (`model/convert.py` makes it).
use std::sync::LazyLock;

use image::{GrayImage, Rgb, RgbImage, imageops};
use rten::Model;
use rten_tensor::{NdTensor, prelude::*};

/// The height the model reads a line of text at.
const HEIGHT: u32 = 48;
/// Shorter lines are padded to this width, as the model was trained.
const WIDTH: u32 = 320;
/// Longer lines are narrowed to this width, as PaddleOCR does; the model's work and memory grow
/// with the width.
const WIDEST: u32 = 3200;
/// The most width the model reads from one image, about half a second's work.
const MOST: u64 = 64 * WIDTH as u64;
/// How far from the background a gray value must be to count as text.
const INK: u8 = 80;
/// Fewer rows of ink than any line of text has, such as a rule.
const THINNEST: u32 = 4;

struct Recognizer {
    model: Model,
    /// By class: none, the characters, then a space.
    classes: Vec<&'static str>,
}

static RECOGNIZER: LazyLock<Result<Recognizer, String>> = LazyLock::new(|| {
    let model = Model::load_static_slice(include_bytes!("../model/recognition.onnx"))
        .map_err(|e| e.to_string())?;
    let classes = std::iter::once("")
        .chain(include_str!("../model/characters.txt").lines())
        .chain([" "])
        .collect();
    Ok(Recognizer { model, classes })
});

/// The text in an encoded image (PNG, JPEG, GIF, WebP or BMP), one line of text to a line; empty
/// when it has none. Fails when the image cannot be decoded, or has more text than is read in one.
pub fn recognize(image: &[u8]) -> Result<String, String> {
    let recognizer = RECOGNIZER.as_ref().map_err(Clone::clone)?;
    let image = on_white(image::load_from_memory(image).map_err(|e| e.to_string())?);
    let lines: Vec<_> = lines(&imageops::grayscale(&image))
        .into_iter()
        .map(|(top, bottom)| {
            let width = (u64::from(image.width()) * u64::from(HEIGHT))
                .div_ceil(u64::from(bottom - top))
                .min(u64::from(WIDEST)) as u32;
            (top, bottom - top, width)
        })
        .collect();
    let work: u64 = lines
        .iter()
        .map(|&(.., width)| u64::from(width.max(WIDTH)))
        .sum();
    if work > MOST {
        return Err(format!("too much text in one image: {} lines", lines.len()));
    }
    let mut text = Vec::new();
    for (top, height, width) in lines {
        let line = imageops::crop_imm(&image, 0, top, image.width(), height).to_image();
        let line = imageops::resize(&line, width, HEIGHT, imageops::FilterType::Triangle);
        text.push(recognizer.line(&line)?);
    }
    Ok(text.join("\n"))
}

/// Transparent parts white, as a page usually shows them; without its alpha, a transparent
/// background is black, under black text.
fn on_white(image: image::DynamicImage) -> RgbImage {
    let image = image.into_rgba8();
    RgbImage::from_fn(image.width(), image.height(), |x, y| {
        let [red, green, blue, alpha] = image.get_pixel(x, y).0.map(u32::from);
        Rgb([red, green, blue].map(|c| ((c * alpha + 255 * (255 - alpha)) / 255) as u8))
    })
}

/// A run of rows with ink, and which columns have ink in them.
struct Band {
    top: u32,
    bottom: u32,
    columns: Vec<bool>,
}

impl Band {
    fn height(&self) -> u32 {
        self.bottom - self.top
    }

    /// How many columns have ink.
    fn width(&self) -> usize {
        self.columns.iter().filter(|&&ink| ink).count()
    }

    fn join(&mut self, other: Band) {
        self.top = self.top.min(other.top);
        self.bottom = self.bottom.max(other.bottom);
        self.columns
            .iter_mut()
            .zip(other.columns)
            .for_each(|(c, ink)| *c |= ink);
    }
}

/// The rows of each line of text, split where a whole row is background; the model reads one line
/// at a time. The whole image when no row stands out.
fn lines(gray: &GrayImage) -> Vec<(u32, u32)> {
    let mut counts = [0usize; 256];
    for pixel in gray.pixels() {
        counts[usize::from(pixel.0[0])] += 1;
    }
    let background = (0..=u8::MAX).fold(0, |most, v| {
        if counts[usize::from(v)] >= counts[usize::from(most)] {
            v
        } else {
            most
        }
    });
    let mut bands: Vec<Band> = Vec::new();
    for (y, row) in (0..).zip(gray.rows()) {
        let columns: Vec<bool> = row.map(|p| p.0[0].abs_diff(background) > INK).collect();
        if !columns.contains(&true) {
            continue;
        }
        let row = Band {
            top: y,
            bottom: y + 1,
            columns,
        };
        match bands.last_mut() {
            Some(band) if band.bottom == y => band.join(row),
            _ => bands.push(row),
        }
    }
    if bands.is_empty() {
        return vec![(0, gray.height())];
    }
    // A band much shorter and narrower than the nearer band beside it and close to it, such as the
    // dots over i and j or an underscore, belongs to it. A line of small text under a large title
    // is short, and may be narrow, but stands apart from it.
    while let Some((short, into)) = (0..bands.len()).find_map(|i| {
        let above = i
            .checked_sub(1)
            .map(|j| (j, bands[i].top - bands[j].bottom));
        let below = bands
            .get(i + 1)
            .map(|next| (i + 1, next.top - bands[i].bottom));
        let (j, gap) = above.into_iter().chain(below).min_by_key(|&(_, gap)| gap)?;
        let line = &bands[j];
        (bands[i].height() * 3 < line.height()
            && bands[i].width() * 3 < line.width()
            && gap * 2 < line.height())
        .then_some((i, j))
    }) {
        let band = bands.remove(short);
        bands[if into > short { into - 1 } else { into }].join(band);
    }
    bands.retain(|band| band.height() >= THINNEST);
    // Room around each line, as the model was trained with.
    bands
        .into_iter()
        .map(|Band { top, bottom, .. }| {
            let margin = (bottom - top) / 4 + 1;
            (
                top.saturating_sub(margin),
                (bottom + margin).min(gray.height()),
            )
        })
        .collect()
}

impl Recognizer {
    /// The text in a line of text the height the model reads.
    fn line(&self, line: &RgbImage) -> Result<String, String> {
        let mut input =
            NdTensor::<f32, 4>::zeros([1, 3, HEIGHT as usize, line.width().max(WIDTH) as usize]);
        for (x, y, pixel) in line.enumerate_pixels() {
            // BGR scaled to [-1, 1], as PaddleOCR feeds it.
            for (channel, value) in [pixel[2], pixel[1], pixel[0]].into_iter().enumerate() {
                input[[0, channel, y as usize, x as usize]] = f32::from(value) / 127.5 - 1.0;
            }
        }
        let output: NdTensor<f32, 3> = self
            .model
            .run_one(input.view().into(), None)
            .map_err(|e| e.to_string())?
            .try_into()
            .map_err(|e: rten::TryFromValueError| e.to_string())?;
        // The likeliest class at each step; a class repeated with no "none" between is one
        // character.
        let mut text = String::new();
        let mut previous = 0;
        for step in 0..output.size(1) {
            let (class, _) = output.slice((0, step)).iter().enumerate().fold(
                (0, f32::MIN),
                |best, (class, &p)| if p > best.1 { (class, p) } else { best },
            );
            if class != previous && class != 0 {
                text.push_str(self.classes[class]);
            }
            previous = class;
        }
        Ok(text.trim().to_owned())
    }
}
