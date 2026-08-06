# LLM 连通性诊断

## 目标
- 把 LLM 检测拆成两步：先做基础连通性，再做真实模型可用性。
- 排错信息直接返回给前端，不再只给一条模糊报错。

## 接口

### 1. 获取模型列表
- `GET /api/llm-profiles/{id}/models`
- 作用：
  - 验证 `API Base URL` 是否可达
  - 验证 `API Key` 是否可用
  - 验证当前 `model_name` 是否出现在模型列表里
- 请求地址规则：
  - 严格使用用户填写的 `API Base URL`
  - 只在末尾追加 `/models`
  - 不自动补 `/v1`
- 结果字段：
  - `ok`
  - `message`
  - `request_url`
  - `status_code`
  - `duration_ms`
  - `models`
  - `selected_model_available`
  - `consumes_tokens=false`

### 2. 测试模型
- `POST /api/llm-profiles/{id}/test`
- 作用：
  - 用极小请求验证当前模型和推理端点是否真的可用
- 请求地址规则：
  - 优先尝试 `API Base URL + /chat/completions`
  - 如果返回 `404`，自动回退尝试 `API Base URL + /responses`
- 结果字段：
  - `ok`
  - `message`
  - `request_url`
  - `attempted_urls`
  - `endpoint_kind`
  - `status_code`
  - `duration_ms`
  - `prompt_tokens`
  - `completion_tokens`
  - `total_tokens`
  - `response_preview`
  - `consumes_tokens=true`

## 前端展示
- 个人页保留两个独立动作：
  - `获取模型列表`
  - `测试模型`
- 展示重点：
  - HTTP 状态码
  - 请求耗时
  - 最终请求 URL
  - 当前是否耗 token
  - 当前模型是否命中模型列表
  - 如果做了真实调用，再展示实际 token 用量

## 适配说明
- 对火山 Ark 这类 `https://.../api/v3` 风格地址，不再自动补 `/v1`。
- `404` 错误会明确带上最终请求 URL，方便直接定位是 Base URL、端点路径还是模型名问题。
