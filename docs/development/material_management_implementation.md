# 统一材料管理维护说明

材料是全局库，身份仅保留自己的默认材料选择。产品语义见 [统一材料管理设计](../product/material_management_design.md)。

## 实现入口

| 职责 | 位置 |
| --- | --- |
| HTTP 接口与文件响应 | [materials/api.py](../../backend/app/modules/identities/materials/api.py) |
| 上传、默认材料、删除与引用处理 | [materials/service.py](../../backend/app/modules/identities/materials/service.py) |
| 全局材料查询 | [material_catalog.py](../../backend/app/services/material_catalog.py) |
| 文件写入 | [file_storage.py](../../backend/app/services/file_storage.py) |
| 前端 API | [materials.ts](../../frontend/src/lib/api/materials.ts) |

新增上传通过 `save_upload(file, "materials")` 写入 `uploads/materials/`，使用 UUID 文件名。数据库保留原文件名、大小、哈希与展示信息；文本按需提取。旧文件路径继续由各材料记录持有。

`GET/POST /api/materials` 面向全局库；身份路径上传接口仍保留。带身份上传时，如果该身份尚无默认材料且文件可用作参考材料，会自动设为默认。具体请求与响应字段以 schemas 和 API 为准。

删除先锁定材料并检查当前引用；存在阻塞任务或预览指纹过期时拒绝。可清理的引用、默认材料选择和数据库记录在同一事务内处理，提交后删除实体文件。删除影响预览与执行共用 service 中的规则。

## 迁移历史

- [b1f4f0d34c6a](../../backend/alembic/versions/b1f4f0d34c6a_add_identity_materials.py)：引入统一材料表，回填旧简历与附件，并映射任务选择。
- [c8d7e1a42b90](../../backend/alembic/versions/c8d7e1a42b90_drop_legacy_material_fields.py)：移除旧附件表与旧字段。
- [20260811_global_material_library](../../backend/alembic/versions/20260811_global_material_library.py)：转为全局材料库。

升级现有库运行 `uv run alembic upgrade head`。维护时重点验证旧库迁移、跨身份材料访问、任务材料快照，以及删除预览和执行对引用变化的一致处理；已有覆盖位于 [test_identity_material_module.py](../../backend/test/test_identity_material_module.py)。
