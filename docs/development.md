# 开发约定

[AGENTS.md](../AGENTS.md) 的原则落到这个项目上的具体做法。能由工具检查的写在 lint 和编译配置里，不在这里重复。

## 目录

| 目录 | 内容 |
|---|---|
| `crates/db` | 迁移和打开数据库 |
| `crates/net` | 网络：系统代理、TLS 设置 |
| `crates/mentor` | 导师：列表、搜索、编辑、标签、回收站 |
| `crates/mailbox` | 邮箱：服务商表、收发信的连接和测试 |
| `crates/model` | 模型：服务商表、调用和测试、Token 用量 |
| `crates/profile` | 个人资料和信件样式 |
| `crates/material` | 材料：存文件、提取文字 |
| `crates/template` | 模板和占位符 |
| `crates/email` | 邮件：草稿和状态 |
| `crates/send` | 发信：常驻的发信任务、邮箱轮转 |
| `crates/check` | 发送前检查：规则和收件域名 |
| `crates/writing` | AI 写信：给模型的资料和规则，解析写好的信 |
| `crates/fakemail` | 测试用的假 SMTP 和 IMAP 服务器 |
| `crates/api` | 界面、手机和 agent 调用的操作，统一的分发入口；界面用的 TypeScript 类型从这里生成 |
| `crates/app` | Tauri 桌面应用：窗口、数据目录，把界面的调用交给 `api` |
| `xtask` | 仓库命令：生成类型、示例数据、基准 |
| `ui` | 界面；`ui/src/api/types.ts` 是生成的 |

每个领域一个 crate，依赖只朝一个方向：`app` → `api` → 各领域 → `db`。

## 命令

第一次：装好 Rust、Node 和 pnpm，再装 sqlx 的命令行工具：`cargo install sqlx-cli --no-default-features --features sqlite,sqlx-toml`。

| 命令 | 做什么 |
|---|---|
| `pnpm install` | 装依赖 |
| `cargo xtask seed` | 给开发版填示例数据 |
| `pnpm dev` | 启动开发版，界面改了自动刷新 |
| `pnpm check` | 提交前的全部检查：格式、clippy、测试，生成的类型和查询元数据是否最新，界面的类型检查、lint 和测试 |
| `pnpm fmt` | 格式化 Rust 和界面的代码 |
| `pnpm sqlx` | 改了 SQL 或迁移后，重新生成 `.sqlx` 里的查询元数据 |
| `cargo xtask types` | 改了操作或它们的类型后，重新生成 `ui/src/api/types.ts` |
| `cargo xtask bench` | 按[性能预算](data-model.md#性能)的规模测高频查询 |
| `pnpm --dir ui icons` | 从 `docs/icon.svg` 重新生成应用图标 |
| `pnpm package` | 打包安装包 |

- 开发版的数据在仓库的 `.dev-data` 里，应用标识也单独一个（`crates/app/tauri.dev.conf.json`），和装好的应用互不干扰。发布前可以直接改迁移，改了以后开发版会拒绝打开旧的数据库：把 `.dev-data` 移到废纸篓，再运行 `cargo xtask seed`。
- 查询在编译时检查：编译读 `.sqlx` 里的元数据，不用连数据库。改了 SQL 没重新生成，编译会报错。
- 界面用 TypeScript 7。typescript-eslint 还不支持它，lint 用 oxlint 和它的类型感知规则。
- 界面的测试用 Vitest，和代码放在一起（`*.test.ts`）；要用 DOM 的在文件开头声明 happy-dom 环境。
- CI 在 macOS 和 Windows 上各跑一遍 `pnpm check`，master 要求两个都通过才能合并。PR 准备好后用 `gh pr merge --auto --squash` 设成自动合并，不用等 CI 跑完。

## 代码

- 规则多的逻辑写成纯函数，不碰数据库和网络：服务层读数据、调用纯函数、写回结果。
- 用普通的函数、模块和枚举。只有一种实现时不用 trait 对象，也不建类型层次。可变的全局状态只放在少数明确的地方。
- 不建 `utils`、`helpers`、`common` 这类杂物模块，不写只做转发导出的文件。
- 不可能缺失的值不写成可选：TypeScript 里不对它用 `?.` 和 `??`，Rust 里不包成 `Option`。
- 路径用 `std::path` 和 `node:path` 拼接。仓库脚本只写一份，用 Rust（xtask）或 Node 运行的 TypeScript，不成对写 `.sh` 和 `.ps1`。
- 依赖：JavaScript 只用 pnpm，用 `pnpm add` 加依赖，一次性的工具用 `pnpm dlx`，不手改版本号；Rust 用 `cargo add`。

## 异步和后台任务

- 耗 CPU 的工作（解析 HTML、PDF）放到 `spawn_blocking` 或专门的线程池，不占异步执行线程。
- 后台任务出错时，把错误记在任务上给用户看。只捕获能处理的错误，比如唯一约束冲突转成"已存在"。
- 计时和超时用单调时钟。电脑睡眠唤醒后，重新检查到期的定时发送。

## 时间

- 分清两种时间：时刻存 UTC；用户说的"某天几点"按用户时区理解（见[数据模型](data-model.md#通用约定)）。
- 定时窗口按用户时区的日期和起止时间排，排好的计划发送时间存成时刻；显示时换成用户时区；模板里的日期按用户时区。
- 测试里固定时区和当前时间，覆盖跨午夜、窗口末尾和夏令时。

## 界面

- 用户输入的东西不丢：
  - 有未保存内容的弹窗和抽屉，点外面、拖选到外面松手、按 Esc 都不直接关闭。
  - 批量审核时切换草稿前先保存。
  - 列表的筛选、排序和翻页放进网址（TanStack Router 的 search 参数），返回时还在。
- 界面在 WebKit（macOS 的 WKWebView、iOS Safari）和 Chromium（Windows 的 WebView2、Android）上都要测。
- 组件用 shadcn/ui：`pnpm --dir ui exec shadcn add <组件>` 加到 `ui/src/components/ui`，再按 [ui.md](ui.md#样式) 改样式，只留用得上的变体。不运行 `shadcn init`：它会换掉 ui.md 定的颜色变量，还会打包字体。

## 数据库

- 迁移只往后加，发布后不改已有的迁移。
- 每次发布前，用上一版的真实数据库（去掉个人信息后作为测试数据）测一遍升级。
- 新的高频查询先用 `EXPLAIN QUERY PLAN` 确认走了索引；列表只取要显示的列，并且分页。
- 编号用 `INTEGER PRIMARY KEY AUTOINCREMENT`：删掉的编号不再分配给新的一行，界面里按编号缓存的数据和进行中的保存就不会把两样东西混在一起。
- 一次要读好几条语句的（比如列表先取本页的编号再取行），放在同一个只读事务里，读到的是同一个时刻的数据。
- 写事务的第一条语句就是写入：在默认的延迟事务里先读后写，别的连接恰好在中间提交时会立即报"database is locked"，不会等待。冲突靠唯一约束发现，失败后再查原因告诉用户。
- 一条语句里的参数要么都写 `?`，要么都写 `?1` 这样的编号：两种混用时 sqlx 绑定的位置和 SQLite 算的不一样，会把值绑错。
- 可选的筛选条件写成 `(?1 IS NULL OR …)`，一条查询就能在编译时检查。条件只引用列表的主表，别的表上的条件写成 `IN` 子查询：SQLite 每次查询只算一遍这个集合，不会为主表的每一行连接一次别的表。
- 基准：`cargo xtask bench` 生成[数据模型](data-model.md#性能)里那个规模的数据，测高频查询并检查预算；新的高频查询写好后加进去。改表结构或高频查询时跑一遍，超出预算当 bug 修。

## 测试

- 值得写的：
  - 邮件状态的转换。
  - 定时窗口和时区。
  - 发信不重复、不遗漏，包括发送途中应用退出。
  - 回信归到对应的导师，包括一封信属于几位导师、自动回复和退信；红点在点开和在别处回信后消失。
  - 折叠回信里引用的旧内容，用各家邮箱真实的回信做测试数据。
  - 导入时的查重和合并。
  - Markdown 渲染成邮件的 HTML 和纯文本，以及占位符替换。
  - 发送前检查的规则。
  - 解析模型输出和抓取结果。
  - 数据迁移，备份和恢复；联系汇总表在各种操作后和现算视图一致。
  - 修过的 bug。
- 测试数据：保存下来的网页、固定的模型响应、本地的假邮件服务器（`crates/fakemail`：拒收名为 `unknown` 的收件人，收下给 `hangup` 的邮件后不回复就断开）。

## 手动检查

自动化测试覆盖不到、但出过问题的环境，发布前手动过一遍：

- 代理：开着 Clash 的系统代理、SOCKS 代理和 fake-ip（TUN）模式，各试一遍抓取、模型调用和收发信。
- Windows（在干净的虚拟机里）：
  - 全新安装、从上一版升级、卸载（含选择删除本地数据）都走一遍。
  - 没装过 VC++ 运行库也能启动。
  - CLI 的中文输出在 PowerShell 5.1、PowerShell 7 和 cmd 里都不乱码。
  - 装好 CLI 后，新开的终端能找到命令。
