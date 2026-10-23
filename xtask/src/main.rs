//! Repository commands: `cargo xtask <command>`.

mod bench;
mod sample;

use std::path::{Path, PathBuf};
use std::process::ExitCode;

const USAGE: &str = "用法：cargo xtask <命令>

  types [--check]  生成界面用的 TypeScript 类型（ui/src/api/types.ts）；--check 只检查是否最新
  seed             给开发版（.dev-data）填上示例数据
  bench            按 docs/data-model.md 的规模生成数据，测首页查询和写邮件状态的耗时";

#[tokio::main]
async fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    let result = match args.as_slice() {
        ["types"] => types(false),
        ["types", "--check"] => types(true),
        ["seed"] => seed().await,
        ["bench"] => bench::run(&root().join("target/bench.db")).await,
        _ => Err(USAGE.into()),
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("{e}");
            ExitCode::FAILURE
        }
    }
}

type Result<T = ()> = std::result::Result<T, Box<dyn std::error::Error>>;

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn types(check: bool) -> Result {
    let path = root().join("ui/src/api/types.ts");
    let generated = api::typescript();
    if !check {
        return Ok(std::fs::write(&path, generated)?);
    }
    if std::fs::read_to_string(&path)? != generated {
        return Err(format!(
            "{} 不是最新的，运行 cargo xtask types 重新生成",
            path.display()
        )
        .into());
    }
    Ok(())
}

async fn seed() -> Result {
    let dir = root().join(".dev-data");
    std::fs::create_dir_all(&dir)?;
    let db = db::open(&dir.join("app.db")).await?;
    if sqlx::query_scalar::<_, i64>("SELECT count(*) FROM mentor")
        .fetch_one(&db)
        .await?
        > 0
    {
        return Err("开发版的数据库里已经有数据。要重新生成，先把 .dev-data 移到废纸篓。".into());
    }
    sample::fill(
        &db,
        sample::Scale {
            mentors: 300,
            emails: 150,
            messages: 50,
        },
        sample::now(),
    )
    .await?;
    println!("已生成 300 位导师和一些往来邮件");
    Ok(())
}
