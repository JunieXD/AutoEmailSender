# 依赖约定

按业务能力组织代码，优先复用已有公共入口。只有实际共享的能力才需要抽象；目录层级本身不要求增加转发文件或适配层。

- Backend 的 `modules/<domain>/public.py` 提供领域间常用接口，HTTP router 负责请求适配。避免业务逻辑反向依赖 HTTP 层。
- Frontend 页面和 Provider 负责组合功能，共用 API、组件和 hooks 放在现有的对应目录。跨 feature 协作根据实际职责决定，不维护文件级导入豁免名单。
- CLI 通过版本化 Agent API 操作应用，不导入后端实现或直接连接数据库。
- Desktop 保持 main、preload 和 renderer 的进程隔离；共用 IPC 类型与 channel，避免循环依赖。入口文件不限制语句数或固定导入数量。

已有懒加载有具体的初始化原因：`app.schemas` 的通信组 DTO，以及 campaigns、communications 的高层用例，会涉及模型、schema 与领域入口之间的循环依赖。修改这些入口时检查导入顺序，不机械改成急加载。

类型、API/IPC 合同、数据库迁移和业务行为由对应检查覆盖。代码审查关注职责和循环依赖，不要求为每次文件移动更新架构基线。
