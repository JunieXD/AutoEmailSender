# 官网智能抓取实测数据更新

官网的“智能抓取实测”页面使用 `website/data/crawl-benchmark.json`。该文件只能包含学院级聚合数据，禁止写入导师姓名、邮箱、API Key、原始错误、调试日志或数据库文件。

## 更新数据

在仓库根目录运行：

```bash
cd backend
uv run python ../scripts/data/update_crawl_benchmark.py
```

脚本会自动寻找桌面应用数据库，只读查询当前抓取任务，并合并输出文件中已有的其他电脑记录与早期 XLSX 记录。它不会修改原数据库，也不要求先把候选导师导入导师库。首次导入历史表时使用：

```bash
cd backend
uv run python ../scripts/data/update_crawl_benchmark.py \
  --legacy-xlsx "/absolute/path/to/智能抓取测试记录.xlsx"
```

需要指定其他数据库时传入 `--database /absolute/path/to/auto_email_sender.db`。

学校或学院曾使用简称、旧称时，在 `config/crawl-benchmark-aliases.json` 中维护统一名称。更新脚本会在发布阶段同时规范数据库记录和历史 XLSX 记录，不会修改用户的原始任务数据库。例如当前会将“中科院”统一为“中国科学院大学”，并把“沈阳自动化所”统一为“中国科学院沈阳自动化研究所”。

## 公开规则

- `needs_review`、`partially_completed` 和 `completed` 且候选数大于零的任务显示为“已实测”。
- 抓取失败或候选数为零的任务显示为“正在适配”。
- 排队、运行、暂停、主动取消和已删除任务不公开。
- 学校或学院使用明显的内部占位名称时自动排除。
- 页面默认按“学校 + 学院”选取最新一次记录，并允许查看同一目标的历史测试。
- 字段覆盖率只表示字段非空，不表示经过人工核验的准确率。
- 所有候选导师都视为需要详情补全；页面中的补全进度按“成功补全人数 / 候选导师总数”计算。未发起补全的候选仍留在分母中。
- 补全在待审核阶段直接关联原抓取任务。之后对同一任务继续发起部分或全部补全，再次更新官网时会覆盖该任务上次的公开统计，不会新增一条重复记录。

## 多台电脑更新

公开数据使用 Schema 3，并按稳定记录 ID 合并：

- 不同电脑即使本地任务 ID 相同，只要任务来源或创建时间不同，也会保留为不同记录。
- 从一台电脑复制数据库到另一台电脑并继续补全同一任务时，会识别为同一记录并更新原统计。
- 每次生成数据前，必须先让当前分支包含远端最新的 `website/data/crawl-benchmark.json`。否则当前电脑不知道另一台电脑刚上传了什么，仍可能生成一份不完整的文件。

推荐顺序是：先同步远端最新代码，再运行更新脚本、测试、提交和推送。如果推送因另一台电脑抢先更新而被拒绝，先获取远端最新文件，再重新运行更新脚本；不要直接选择任意一边的 JSON，也不要手工删掉冲突记录。

稳定记录 ID 从 Schema 2 开始提供。Schema 3 删除了已经失去区分意义的 `runtimeVersion` 字段；更新脚本会在重新发布时自动清理旧记录中的该字段。Schema 1 数据仍应先在保存主要抓取记录的电脑上完整重建一次。

## Schema 3 补全字段

- `enrichmentSelectedCount`：已经发起过补全的候选人数。
- `enrichmentSucceededCount`：成功补全人数。
- `enrichmentPendingCount`：等待、处理中或可重试失败人数。
- `enrichmentFailedCount`：最终失败人数。

早期 XLSX 没有补全任务信息，因此这些字段为 `null`；数据库记录没有发起补全时为 `0`。

## 发布前检查

```bash
cd backend
uv run python -m unittest test.test_crawl_benchmark_publication

cd ../website
npm ci
npm run test
npm run build
```

确认本地页面无误后再提交 `website/data/crawl-benchmark.json`。不要提交 `auto_email_sender.db` 或任何抓取调试文件。
