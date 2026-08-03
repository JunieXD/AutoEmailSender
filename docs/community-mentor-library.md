# 社区导师库应用集成

## 当前状态

社区导师库由独立公共仓库维护：

- 仓库：<https://github.com/JunieXD/AutoEmailSender-MentorData>
- 发布入口：<https://juniexd.github.io/AutoEmailSender-MentorData/latest.json>
- 应用页面：`/community`

应用只读取 GitHub Pages 上的版本化发布数据。投稿、批量投稿和错误反馈必须进入系统浏览器中的 GitHub Issue Forms；应用不会持有 GitHub Token，也不会代表用户提交内容。

## 用户流程

### 浏览和导入

1. 应用读取并校验 `latest.json`、`manifest.json`、`catalog.json` 和 `revocations.json`。
2. 用户按学校和学院选择需要的分片，一次最多 20 个学院。
3. 应用在本地完成姓名、邮箱、学校、学院、研究方向和状态搜索。
4. 用户选择导师并生成导入预览。
5. 新记录默认新增；本地空字段默认由社区补全；本地非空字段默认保留。
6. 本地和社区同时修改的字段逐项选择后再提交。

标签、个人备注、任务、发送记录、通信历史和匹配结果不会被下载、上传或覆盖。

### 贡献和反馈

- 导师编辑页的“贡献到社区”会把公开职业字段复制到剪贴板，然后用系统浏览器打开单条投稿表。
- “导出社区共享包”生成仓库批量投稿器可直接读取的普通 XLSX；表头严格为社区安全字段，不包含公式、宏、标签或本地私有数据。
- 社区导师卡片的“反馈错误”会复制当前社区值和稳定记录 ID，然后打开错误反馈表。反馈本身不会直接修改数据。

## 实体与重复处理

邮箱不是导师实体 ID。社区使用不透明、稳定的 `mentor_*` ID；邮箱只在第一次导入时作为候选匹配线索。

首次邮箱匹配有以下保护：

- 邮箱相同但姓名不同，必须人工确认；
- 邮箱相同但学校不同，视为可能调动、双聘或邮箱复用，必须人工确认；
- 同一邮箱匹配多条本地记录时禁止自动导入；
- 本地导师已关联另一条社区记录时，按疑似社区重复实体阻止导入；
- 同一批社区分片出现两个相同主邮箱的实体时，整批拒绝并提示数据异常。

一旦建立关联，即使邮箱、学校或职称变化，后续也只按稳定社区 ID 跟踪。多邮箱和多任职保留在社区记录中供用户查看；默认导入主要当前邮箱和主要当前任职。

## 三方比较

`professor_community_links.imported_snapshot_json` 只保存本地实际采用过的社区字段。下一次检查分别比较：

```text
上次采用的社区值（baseline）
          /                 \
    当前本地值           当前社区值
```

- 本地等于 baseline、社区变化：`remote_modified`，默认采用社区更新；
- 社区等于 baseline、本地变化：`local_modified`，默认保留本地；
- 两边都变且结果不同：`conflict`，要求逐字段选择；
- 本地为空、社区非空：`fill_available`；
- 两边相同：`linked_unchanged`。

用户明确保留本地的字段不会写入社区快照，因此以后继续视为本地自主管理字段。

## 生命周期

社区记录支持 `active`、`retired`、`departed`、`deceased`、`stale`、`disputed` 和 `removed`。

退休、离职、去世、争议或撤销记录不会继续出现在正常学院分片，而会进入 `revocations.json`。应用会更新关联表中的远端状态并显示证据提醒，但不会：

- 删除本地导师；
- 自动归档本地导师；
- 恢复已经在本地回收站中的导师；
- 修改历史任务和通信记录。

## 下载与缓存安全

缓存目录为用户数据目录下的 `community-mentor-cache`。核心规则：

- 只允许配置的无凭据标准 HTTPS 基地址；
- 禁止绝对路径、反斜杠、`..`、跨源 URL 和重定向；
- `latest.json` 只能指向对应不可变版本目录；
- 校验 Schema 版本、最低应用版本、文件声明大小、实际字节数和 SHA-256；
- 单文件、单次学院数量和总下载大小均有限制；
- Catalog、Manifest 和分片的 ID、路径、版本、生成时间和记录数必须互相一致；
- 只有四个核心文件全部验证成功后才原子更新当前缓存索引；
- 网络失败时只回退到最后一次完整验证成功的缓存。

所有社区文字均作为普通文本交给 React 渲染。应用不会执行社区内容、拼接 Shell 命令或把社区 URL 当作内部文件路径。

## 后端接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/community-mentors/catalog` | 读取缓存目录；`refresh=true` 时刷新网络并回退缓存 |
| `POST` | `/api/community-mentors/records` | 下载所选学院分片并返回本地比较分类 |
| `POST` | `/api/community-mentors/preview` | 对选中稳定 ID 重新生成导入预览 |
| `POST` | `/api/community-mentors/import` | 根据逐字段选择原子写入导师和稳定关联 |
| `GET` | `/api/community-mentors/share-package` | 为所选本地导师导出安全 XLSX |

## 数据库关联

`professor_community_links` 与 `professors` 一对一：

```text
professor_id                 本地主键、级联删除
community_record_id          唯一稳定社区 ID
dataset_version              最近检查的数据集版本
imported_snapshot_json       实际采用的社区字段快照
imported_at                  最近一次导入时间
last_checked_at              最近一次生命周期检查时间
remote_status                远端生命周期状态
remote_revoked_at            远端退休、离职或撤销时间
```

迁移版本为 `20260803_community_links`，前一版本为 `20260730_db_performance`。
