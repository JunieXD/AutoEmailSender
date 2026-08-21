# 投稿批次合同 v1

## 输入 XLSX

每个文件必须只有一个 `community-share` 工作表，且第一行严格为以下十列：

```text
name,email,title,university,school,department,research_direction,recent_papers,profile_url,source_url
```

文件大小不得超过 5 MiB。每个非空数据行必须有姓名、有效邮箱和 `http://` 或 `https://` 的来源 URL。学校和学院字段在同一文件内必须一致；一个文件代表一个投稿单位。

## Manifest

`manifest.json` 的 `schema_version` 为 `1`。`items[].file` 只能是相对路径，`sha256`、`size_bytes` 和 `professor_count` 必须与磁盘内容一致。`batch_id` 是规范化 item 元数据和文件 SHA-256 的 SHA-256 前 16 位，不包含生成时间和本机绝对路径。

投稿状态只能是：`prepared`、`planned`、`submitted`、`unknown`、`failed`、`verified`。未知状态不得自动重试。

