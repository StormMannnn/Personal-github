# vLLM 流式代理

对 vLLM `/v1/chat/completions` 原始 SSE 输出做一层包装，提供干净的逐字流式文本和 token 统计。

## 快速开始

```bash
bash start_proxy.sh start    # 启动（默认 8001 端口）
bash start_proxy.sh status   # 查看状态
bash start_proxy.sh restart  # 重启
bash start_proxy.sh stop     # 停止
```

## API

### POST /ask — 流式问答

```bash
curl -N http://10.17.101.201:8001/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"用Python写一个快速排序"}'
```

#### 请求参数

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `question` | string | 必填 | 用户问题 |
| `temperature` | float | 0.7 | 采样温度 (0.0~2.0) |
| `max_tokens` | int | 1024 | 最大输出 token 数 |
| `think` | bool | false | 是否输出思考链 |

#### 带参数示例

```bash
# 低温度 + 简短回答
curl -N http://10.17.101.201:8001/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"广州在哪个省份？经纬度是多少？","temperature":0.1,"max_tokens":256}'

# 开启思考链
curl -N http://10.17.101.201:8001/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"9.9和9.11哪个大？","think":true}'
```

#### 响应格式（简化 SSE）

```
data: {"t":"快","event":"start"}

data: {"t":"速"}

data: {"t":"排序"}

...

data: {"event":"end","finish_reason":"stop","elapsed_s":3.21,"chunks":156,"total_tokens":384,"prompt_tokens":32,"completion_tokens":352}
```

| 事件 | 说明 |
|------|------|
| `event: start` | 首 token 输出，附带 `event` 标记 |
| 普通 token | 仅含 `t` 字段，内容为 token 文本 |
| `event: end` | 输出结束，包含 token 统计 |
| `event: error` | 出错时的错误信息 |

### GET /health — 健康检查

```bash
curl http://10.17.101.201:8001/health
# {"proxy":"ok","vllm":"up","model":"Qwen3.6-35B-A3B"}
```

### GET / — 服务信息

```bash
curl http://10.17.101.201:8001/
```

## 对比：代理 vs 直接调用 vLLM

| | 直接 vLLM `:8000` | 代理 `:8001` |
|---|:---:|:---:|
| 输出格式 | 原始 SSE (12+ 字段) | 简化 SSE (1-2 字段) |
| 思考链 | 默认输出英文思考 | 默认隐藏 |
| token 统计 | usage 在最后一个 chunk | end 事件直接给出 |
| 错误信息 | 原始 HTTP 错误 | 中文友好提示 |

## 依赖

- Python 3.10+
- conda 环境 `llm-vllm` (已含 fastapi, uvicorn, httpx)
- vLLM 服务运行在 `127.0.0.1:8000`
