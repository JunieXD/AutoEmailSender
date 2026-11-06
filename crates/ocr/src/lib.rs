//! Text in images, read on this computer by the system's own text recognition.

#[cfg(target_os = "macos")]
mod vision;
#[cfg(target_os = "macos")]
pub use vision::{available, recognize};

#[cfg(windows)]
mod media_ocr;
#[cfg(windows)]
pub use media_ocr::{available, recognize};
