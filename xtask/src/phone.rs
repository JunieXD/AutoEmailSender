//! The phone's pages (docs/development.md#手机远程): `cargo xtask phone <目录>` builds them for the
//! development server, `cargo xtask publish-phone <主机>:<目录>` puts them on a server.
use std::path::Path;

use tokio::process::Command;

const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Builds this version's page into `<dir>/<version>/` and the loader into `<dir>/loader/`.
pub async fn build(dir: &Path) -> crate::Result {
    // Vite runs in ui/, which a relative path would be taken from.
    let dir = std::path::absolute(dir)?;
    vite("phone", &dir.join(VERSION)).await?;
    vite("loader", &dir.join("loader")).await
}

async fn vite(mode: &str, out: &Path) -> crate::Result {
    let status = Command::new("node")
        .arg(crate::root().join("ui/node_modules/vite/bin/vite.js"))
        .args(["build", "--mode", mode, "--emptyOutDir", "--outDir"])
        .arg(out)
        .current_dir(crate::root().join("ui"))
        .env("AES_PHONE_VERSION", VERSION)
        .status()
        .await?;
    if !status.success() {
        return Err(format!("构建手机页面失败：{status}").into());
    }
    Ok(())
}

/// Uploads to `target`, `<SSH host>:<directory>`, through `ssh` and `sudo`, as the directory
/// belongs to the server. A version's directory is never changed once there, as phones cache it
/// for good; the loader's page is replaced, so that running it again after an interruption
/// finishes the job.
pub async fn publish(target: &str) -> crate::Result {
    let (host, dir) = target
        .split_once(':')
        .filter(|(host, dir)| {
            !host.is_empty()
                && dir.starts_with('/')
                && dir
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || "/_.-".contains(c))
        })
        .ok_or("目标写成 <SSH 主机>:<绝对路径>，比如 txy:/opt/autoemail-server/phone")?;
    let built = crate::root().join("target/phone");
    build(&built).await?;
    // Unpacked beside the pages and then moved in, so a phone never loads half of them; what an
    // interrupted upload left is cleared first. The version goes in before the loader that leads
    // to it. The loader's new files go in beside its old ones, which a loader page fetched just
    // before may still ask for, and its page last; `rmdir` fails on a file this leaves behind.
    let script = format!(
        "set -e; cd {dir}; rm -rf .incoming; mkdir .incoming; tar -xf - -C .incoming; \
         kept=; if [ -e {VERSION} ]; then kept=1; else mv .incoming/{VERSION} {VERSION}; fi; \
         mkdir -p loader/assets; mv .incoming/loader/assets/* loader/assets/; \
         rmdir .incoming/loader/assets; mv .incoming/loader/index.html loader/; \
         rmdir .incoming/loader; rm -rf .incoming; test -z \"$kept\" || exit 3"
    );
    let mut tar = std::process::Command::new("tar")
        .arg("-cf")
        .arg("-")
        .arg("-C")
        .arg(&built)
        .args([VERSION, "loader"])
        .stdout(std::process::Stdio::piped())
        .spawn()?;
    let archive = tar
        .stdout
        .take()
        .unwrap_or_else(|| unreachable!("stdout is piped"));
    let status = std::process::Command::new("ssh")
        .args([host, &format!("sudo sh -c '{script}'")])
        .stdin(archive)
        .status()?;
    // tar fails too when the server stopped reading, so the server's answer comes first.
    let packed = tar.wait()?;
    match status.code() {
        Some(0 | 3) if !packed.success() => Err(format!("打包手机页面失败：{packed}").into()),
        Some(0) => {
            println!("已发布到 {host}:{dir}/{VERSION} 和 {dir}/loader");
            Ok(())
        }
        Some(3) => {
            println!(
                "{host} 上已经有 {dir}/{VERSION}，没有改动它（同一版本只发布一次）；已换上 {dir}/loader"
            );
            Ok(())
        }
        _ => Err(format!("上传到 {host}:{dir} 失败：{status}").into()),
    }
}
