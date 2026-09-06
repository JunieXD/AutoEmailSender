# 投稿批次合同 v1

## 输入 XLSX

每个文件只能含一个可见的 `community-share` 工作表，第一行严格为以下十列：

```text
name,email,title,university,school,department,research_direction,recent_papers,profile_url,source_url
```

文件不得超过 5 MiB、解压后 32 MiB、50000 条导师。每个非空行必须有姓名、有效邮箱、非空学校及学院、有效 http(s) 来源 URL；同一文件内学校和学院一致。不得含公式、电子表格错误值、额外列、隐藏表或非标准附件部件。不接受带批注、嵌入对象等额外内容的原始工作簿；请重新导出公开字段 XLSX。

## Manifest

`schema_version` 为 `1`，`repository` 是 GitHub `owner/repo`，`license` 为 `CC BY 4.0`。`items[].file` 为 `files/001.xlsx` 形式的直属相对路径，文件路径、SHA-256、学校/学院均不能重复。`files/` 的文件清单必须与 items 完全相同，不允许链接或子目录；SHA-256、大小、行数及单位必须与内容一致。

`batch_id` 是规范化 item 元数据和文件 SHA-256 的 SHA-256 前 16 位，不含生成时间、本机路径、仓库或许可证。仓库与许可证单独显示在计划中，并参与远端 PR 内容核对。更换文件或元数据后重新准备批次；不要手工修改 ID。

状态为 `prepared`、`planned`、`submitted`、`unknown`、`failed`、`verified`、`closed`。本地 `submission.stage` 记录推送/创建 PR 的恢复检查点；状态通过原子替换写回。本地生成时间和错误等信息不会上传，远端 manifest 只含固定公共字段和 `planned` 状态；实际 PR 状态以 GitHub 为准。

提交前从审计清单读取并核对字节，在临时工作区再次审计、核对 Git blob 与完整分支差异后推送。查重同时验证 PR 标记、head/base、仓库以及全部新增文件的 Git blob SHA，不能仅凭标题判断重复。仅支持同仓库维护者 PR；不自动 fork、强推、重开或合并。
