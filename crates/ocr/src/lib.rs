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
/// How far from the background a gray value must be to count as text.
const INK: u8 = 80;

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
/// when it has none. Fails when the image cannot be decoded.
pub fn recognize(image: &[u8]) -> Result<String, String> {
    let recognizer = RECOGNIZER.as_ref().map_err(Clone::clone)?;
    let image = on_white(image::load_from_memory(image).map_err(|e| e.to_string())?);
    let mut text = Vec::new();
    for (top, bottom) in lines(&imageops::grayscale(&image)) {
        let line = imageops::crop_imm(&image, 0, top, image.width(), bottom - top).to_image();
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

/// The rows of each line of text, split where a whole row is background; the model reads one line
/// at a time. A band much shorter than the rest, such as the dots over i and j, joins the nearer
/// line. The whole image when no row stands out.
fn lines(gray: &GrayImage) -> Vec<(u32, u32)> {
    let mut counts = [0usize; 256];
    for pixel in gray.pixels() {
        counts[usize::from(pixel.0[0])] += 1;
    }
    let background = (0..=u8::MAX)
        .max_by_key(|&v| counts[usize::from(v)])
        .unwrap_or(u8::MAX);
    let mut bands: Vec<(u32, u32)> = Vec::new();
    for (y, mut row) in (0..).zip(gray.rows()) {
        if row.any(|pixel| pixel.0[0].abs_diff(background) > INK) {
            match bands.last_mut() {
                Some((_, bottom)) if *bottom == y => *bottom = y + 1,
                _ => bands.push((y, y + 1)),
            }
        }
    }
    if bands.is_empty() {
        return vec![(0, gray.height())];
    }
    while let Some(tallest) = bands.iter().map(|(top, bottom)| bottom - top).max()
        && let Some(short) = bands
            .iter()
            .position(|(top, bottom)| (bottom - top) * 3 < tallest)
    {
        let (top, bottom) = bands.remove(short);
        let above = short.checked_sub(1).map(|i| top - bands[i].1);
        let below = bands.get(short).map(|next| next.0 - bottom);
        let into = match (above, below) {
            (Some(above), Some(below)) if below < above => short,
            (Some(_), _) => short - 1,
            _ => short,
        };
        let band = &mut bands[into];
        *band = (band.0.min(top), band.1.max(bottom));
    }
    // Room around each line, as the model was trained with.
    bands
        .into_iter()
        .map(|(top, bottom)| {
            let margin = (bottom - top) / 4 + 1;
            (
                top.saturating_sub(margin),
                (bottom + margin).min(gray.height()),
            )
        })
        .collect()
}

impl Recognizer {
    fn line(&self, image: &RgbImage) -> Result<String, String> {
        let width = (image.width() * HEIGHT).div_ceil(image.height());
        let resized = imageops::resize(image, width, HEIGHT, imageops::FilterType::Triangle);
        let mut input =
            NdTensor::<f32, 4>::zeros([1, 3, HEIGHT as usize, width.max(WIDTH) as usize]);
        for (x, y, pixel) in resized.enumerate_pixels() {
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
