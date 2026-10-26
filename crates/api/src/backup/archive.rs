//! ZIP encoding and the allowlist for external archive entries.

use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::Path;

use zip::write::SimpleFileOptions;
use zip::{CompressionMethod, ZipArchive, ZipWriter};

use super::{Error, Manifest, Result, sync_dir};

const MANIFEST_LIMIT: u64 = 64 * 1024;
const DATABASE_LIMIT: u64 = 2 * 1024 * 1024 * 1024;
const TOTAL_LIMIT: u64 = 4 * 1024 * 1024 * 1024;
const ENTRY_LIMIT: usize = 100_002;

pub(super) fn invalid(detail: &str) -> Error {
    Error::Invalid(format!(
        "备份文件不完整或格式不正确（{detail}），请重新选择本应用导出的 zip 备份。"
    ))
}

fn zip_error(detail: &'static str, source: zip::result::ZipError) -> Error {
    match source {
        zip::result::ZipError::Io(source) => Error::Io {
            operation: detail,
            source,
        },
        source => Error::Archive {
            detail,
            source: Box::new(source),
        },
    }
}

fn json_error(detail: &'static str, source: serde_json::Error) -> Error {
    if let Some(kind) = source.io_error_kind() {
        Error::Io {
            operation: detail,
            source: std::io::Error::new(kind, source),
        }
    } else {
        Error::Archive {
            detail,
            source: Box::new(source),
        }
    }
}

fn extract(input: &mut dyn Read, output: &mut dyn Write) -> Result<u64> {
    let mut buffer = [0; 8192];
    let mut copied = 0;
    loop {
        let count = input.read(&mut buffer).map_err(|source| Error::Archive {
            detail: "zip 内容损坏",
            source: Box::new(source),
        })?;
        if count == 0 {
            return Ok(copied);
        }
        output
            .write_all(&buffer[..count])
            .map_err(|source| Error::Io {
                operation: "extract file",
                source,
            })?;
        copied += count as u64;
    }
}

pub(super) fn write(
    target: &Path,
    snapshot: &Path,
    materials: &Path,
    ids: &[i64],
    manifest: &Manifest,
) -> Result<()> {
    let mut total = fs::metadata(snapshot)?.len();
    if total > DATABASE_LIMIT || ids.len() + 2 > ENTRY_LIMIT {
        return Err(invalid("数据库超过 2 GB 或材料数量过多"));
    }
    for id in ids {
        let size = fs::metadata(material::file(materials, *id))?.len();
        if size > material::MAX_SIZE as u64 {
            return Err(invalid("材料文件超过 20 MB"));
        }
        total += size;
    }
    if total > TOTAL_LIMIT - MANIFEST_LIMIT {
        return Err(invalid("备份数据超过 4 GB"));
    }
    let parent = target
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let mut temporary = tempfile::Builder::new()
        .prefix(".aes-backup-")
        .tempfile_in(parent)?;
    let mut zip = ZipWriter::new(temporary.as_file_mut());
    let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
    zip.start_file("manifest.json", options)
        .map_err(|e| zip_error("无法写入清单", e))?;
    serde_json::to_writer(&mut zip, manifest).map_err(|e| json_error("无法写入清单", e))?;
    zip.start_file("app.db", options)
        .map_err(|e| zip_error("无法写入数据库", e))?;
    std::io::copy(&mut File::open(snapshot)?, &mut zip)?;
    for id in ids {
        zip.start_file(format!("materials/{id}"), options)
            .map_err(|e| zip_error("无法写入材料", e))?;
        std::io::copy(&mut File::open(material::file(materials, *id))?, &mut zip)?;
    }
    zip.finish().map_err(|e| zip_error("zip 未能写完", e))?;
    temporary.as_file().sync_all()?;
    temporary.persist(target).map_err(|e| e.error)?;
    sync_dir(parent)?;
    Ok(())
}

pub(super) fn manifest(path: &Path) -> Result<Manifest> {
    let file = File::open(path)?;
    if file.metadata()?.len() > MANIFEST_LIMIT {
        return Err(invalid("清单过大"));
    }
    let manifest: Manifest =
        serde_json::from_reader(file).map_err(|e| json_error("清单无法读取", e))?;
    if manifest.app_version.is_empty()
        || manifest.app_version.len() > 100
        || manifest.exported_at < 0
    {
        return Err(invalid("清单内容无效"));
    }
    Ok(manifest)
}

pub(super) fn unpack(source: &Path, stage: &Path) -> Result<Manifest> {
    let file = File::open(source)?;
    if file.metadata()?.len() > TOTAL_LIMIT {
        return Err(invalid("文件超过 4 GB"));
    }
    let mut zip = ZipArchive::new(file).map_err(|e| zip_error("不是可读取的 zip 文件", e))?;
    if zip.len() > ENTRY_LIMIT {
        return Err(invalid("文件数量过多"));
    }
    fs::create_dir_all(stage.join("materials"))?;
    let mut names = BTreeSet::new();
    let mut total = 0;
    for i in 0..zip.len() {
        let mut entry = zip
            .by_index(i)
            .map_err(|e| zip_error("zip 条目无法读取", e))?;
        let name = entry
            .name()
            .map_err(|e| zip_error("文件名编码无效", e))?
            .into_owned();
        // Exact portable names also reject backslashes, drive prefixes, aliases and case
        // collisions.
        let limit = match name.as_str() {
            "manifest.json" => MANIFEST_LIMIT,
            "app.db" => DATABASE_LIMIT,
            "materials/" if entry.is_dir() => 0,
            _ => {
                let id = name
                    .strip_prefix("materials/")
                    .and_then(|n| n.parse::<i64>().ok())
                    .filter(|id| *id > 0 && name == format!("materials/{id}"));
                if id.is_none() {
                    return Err(invalid("包含不支持的文件名或越界路径"));
                }
                material::MAX_SIZE as u64
            }
        };
        if !names.insert(name.clone())
            || entry.enclosed_name().is_none()
            || entry.is_symlink()
            || entry.size() > limit
        {
            return Err(invalid("文件重复、路径无效或单个文件过大"));
        }
        total += entry.size();
        if total > TOTAL_LIMIT {
            return Err(invalid("解压后超过 4 GB"));
        }
        if name == "materials/" {
            continue;
        }
        let mut file = File::options()
            .write(true)
            .create_new(true)
            .open(stage.join(&name))?;
        let size = entry.size();
        let copied = extract(&mut entry.by_ref().take(limit + 1), &mut file)?;
        if copied != size {
            return Err(invalid("文件大小与 zip 记录不符"));
        }
        file.flush()?;
    }
    if !names.contains("manifest.json") || !names.contains("app.db") {
        return Err(invalid("缺少清单或数据库"));
    }
    manifest(&stage.join("manifest.json"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::error::Error as _;

    #[test]
    fn extraction_preserves_output_errors_and_existing_contents() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("output");
        fs::write(&path, b"previous contents").unwrap();
        let mut output = File::open(&path).unwrap();
        let error = extract(&mut &b"database"[..], &mut output).unwrap_err();
        assert!(matches!(&error, Error::Io { source, .. } if source.raw_os_error().is_some()));
        assert!(error.source().is_some());
        assert_eq!(fs::read(path).unwrap(), b"previous contents");
    }
}
