# MewCode 学习笔记：Agent 与工具调用机制

整理日期：2026-10-14。

本文根据当前项目源码整理，记录本轮学习涉及的模块职责和工具执行流程。示例用于说明机制，省略的字段和逻辑会明确标注；本文没有修改业务代码。

## 目录

1. 项目中的核心模块如何分工
2. agents 文件夹主要做什么
3. serialization.py 的作用
4. Grep 与 ReadFile 的区别
5. ReadFile 如何把内容交给 LLM
6. Python 工具如何转换成 JSON Schema
7. 工具的并发执行规则
8. 工具失败后如何让 Agent 继续运行
9. 源码阅读顺序与复习要点

## 1. 项目中的核心模块如何分工

先建立一条整体链路：

```text
对话历史 ConversationManager
        ↓
serialization.py 转换消息格式
        ↓
client.py 请求模型 API，同时提供工具定义
        ↓
LLM 返回文本或工具调用
        ↓
Agent 查找工具、检查权限、校验参数并执行
        ↓
工具结果加入对话历史
        ↓
再次请求 LLM，由模型分析结果并决定下一步
```

| 模块 | 主要职责 |
|---|---|
| [conversation.py](../mewcode/conversation.py) | 定义内部消息结构，管理对话历史。 |
| [serialization.py](../mewcode/serialization.py) | 将内部消息转换成各模型 API 使用的消息格式。 |
| [client.py](../mewcode/client.py) | 请求模型 API，解析流式响应。 |
| [agent.py](../mewcode/agent.py) | 驱动模型调用和工具执行循环。 |
| [tools/](../mewcode/tools/) | 定义工具、参数、注册表及具体执行逻辑。 |
| [agents/](../mewcode/agents/) | 管理子智能体的配置、工具范围、后台任务、通知和追踪。 |
| [teams/](../mewcode/teams/) | 管理团队成员、共享任务和消息通信。 |

可以记成：**对话层存消息，序列化层转格式，客户端请求模型，Agent 调度工具。**

## 2. agents 文件夹主要做什么

实际目录是 `mewcode/agents/`。它负责子智能体系统，让主智能体可以委派搜索、规划、验证等任务，并接收结果。

### 2.1 各文件职责

| 文件 | 主要职责 |
|---|---|
| [parser.py](../mewcode/agents/parser.py) | 解析 Markdown 中的 YAML 头部和正文，生成 `AgentDef`。 |
| [loader.py](../mewcode/agents/loader.py) | 发现并加载角色配置，处理同名覆盖，支持已有自定义配置的读取时热重载。 |
| [tool_filter.py](../mewcode/agents/tool_filter.py) | 根据公共规则、后台白名单、角色配置等生成工具集，还包含团队和协调者的工具规则。 |
| [fork.py](../mewcode/agents/fork.py) | 深拷贝父对话历史并追加子任务，检查并禁止再次 fork。 |
| [task_manager.py](../mewcode/agents/task_manager.py) | 启动和管理后台任务，保存状态、结果、耗时、Token 用量，支持取消。 |
| [notification.py](../mewcode/agents/notification.py) | 把后台任务结果格式化为 `<task-notification>`，注入主对话。 |
| [trace.py](../mewcode/agents/trace.py) | 保存智能体父子关系、状态、时间和 Token 数据，为 `/trace` 等功能提供数据。 |
| [__init__.py](../mewcode/agents/__init__.py) | 统一导出相关类和函数。 |

`AgentDef` 描述一个角色的名称、用途、提示词、模型、工具范围、最大轮数、权限模式、是否后台执行、隔离方式等。

配置加载优先级如下，同名角色以优先级高的为准：

```text
项目级 .mewcode/agents/
        ↓
用户级 ~/.mewcode/agents/
        ↓
内置 mewcode/agents/builtins/
```

当前插件加载接口仍是预留实现。

### 2.2 内置角色

| 角色 | 用途 | 当前配置特点 |
|---|---|---|
| [Explore](../mewcode/agents/builtins/explore.md) | 只读搜索代码、定位文件和调用关系。 | 指定 `haiku` 模型，提示词要求只读。 |
| [Plan](../mewcode/agents/builtins/plan.md) | 分析需求、制定实现计划。 | 提示词要求只读，最大 15 轮。 |
| [general-purpose](../mewcode/agents/builtins/general-purpose.md) | 执行通用子任务。 | 使用通用能力，仍受公共工具限制。 |
| [Verification](../mewcode/agents/builtins/verification.md) | 运行构建、测试等检查并给出结论。 | 后台执行；内置角色默认不加载，需要启用对应配置。 |

这些角色主要由 Markdown 配置和提示词定义，执行时复用 `Agent` 类。

### 2.3 谁真正创建和执行子智能体

创建入口在 [tools/agent_tool.py](../mewcode/tools/agent_tool.py)：

```text
主智能体调用 Agent 工具
        ↓
加载角色定义，或复制父对话
        ↓
选择模型，配置工具和权限
        ↓
创建子 Agent
        ↓
前台执行并返回结果，或交给 TaskManager 后台执行
```

需要区分两种上下文：

- 指定 `subagent_type`：普通角色子智能体使用新对话。
- 未指定类型且启用 fork：复制父对话历史，当前实现会自动后台执行。

**上下文独立与文件隔离是两件事。** 普通子智能体默认沿用父智能体工作目录；走 worktree 创建路径时才获得独立的 Git 工作目录。

## 3. serialization.py 的作用

**把项目内部统一的消息转换成各模型 API 需要的格式，可以理解为消息格式翻译器。**

内部的 `Message` 包含：

- `role`：消息角色。
- `content`：文本内容。
- `tool_uses`：模型发起的工具调用。
- `tool_results`：工具执行结果。
- `thinking_blocks`：模型返回的思考内容及相关标识。

这些结构定义在 [conversation.py](../mewcode/conversation.py)，发送前由 [serialization.py](../mewcode/serialization.py) 转换。

### 3.1 各转换函数

| 函数 | 对应协议 | 工具调用与结果格式 |
|---|---|---|
| `build_anthropic_messages()` | Anthropic | `tool_use` / `tool_result`。 |
| `build_openai_input()` | OpenAI Responses | `function_call` / `function_call_output`。 |
| `build_chat_completion_messages()` | Chat Completions | `tool_calls` / `role: "tool"`。 |
| `build_messages()` | 按 `protocol` 分派 | 选择上述转换函数。 |

在本项目中，`"openai"` 对应 Responses，`"openai-compat"` 对应 Chat Completions，默认分支使用 Anthropic 格式。

### 3.2 同一工具结果的不同表示

假设工具调用 ID 是 `call_001`，返回文本是 `main.py`。

Anthropic 格式：

```json
{
  "role": "user",
  "content": [{
    "type": "tool_result",
    "tool_use_id": "call_001",
    "content": "main.py",
    "is_error": false
  }]
}
```

Chat Completions 格式：

```json
{
  "role": "tool",
  "tool_call_id": "call_001",
  "content": "main.py"
}
```

调用 ID 把结果与之前的工具调用对应起来。

这个模块还负责适配思考内容、编码工具参数、合并 Anthropic 连续用户纯文本消息，并支持 Anthropic 的结构化工具结果。

**注意：这个文件处理发给模型的消息格式；会话落盘另有实现。**

## 4. Grep 与 ReadFile 的区别

**Grep 用来定位代码，ReadFile 用来阅读代码及其上下文。**

| 对比项 | [Grep](../mewcode/tools/grep.py) | [ReadFile](../mewcode/tools/read_file.py) |
|---|---|---|
| 主要用途 | 按关键词或正则搜索文件内容。 | 读取指定文件的连续内容。 |
| 操作范围 | 目录下的多个文件，递归搜索。 | 一个明确的文件。 |
| 主要参数 | `pattern`、`path`、`include`。 | `file_path`、`offset`、`limit`。 |
| 返回内容 | 匹配到的行。 | 指定范围内的所有行。 |
| 输出格式 | `相对文件路径:行号:内容`。 | `行号\t内容`。 |
| 典型用途 | 查函数定义、调用位置、错误文本。 | 查看完整函数和前后逻辑。 |
| 已读状态记录 | 不记录。 | 注入状态缓存时记录。 |

两者都属于 `category = "read"` 的工具。

### 4.1 配合使用的例子

先搜索 `build_messages`：

```json
{
  "pattern": "build_messages",
  "path": "mewcode",
  "include": "*.py"
}
```

Grep 的匹配结果可以帮助找到定义和调用位置。找到定义后，再读取对应代码：

```json
{
  "file_path": "mewcode/serialization.py",
  "offset": 151,
  "limit": 6
}
```

`offset` 从 0 开始，因此 `151` 对应文件第 152 行。这里的行号基于整理时的源码，后续修改可能使其变化。

### 4.2 实现细节

- Grep 使用 `Path.glob()` 遍历文件，再使用 Python `re` 逐行匹配；没有调用系统 `grep` 或 `rg`。
- Grep 会跳过 `.git`、`.venv`、`node_modules`、`__pycache__` 等预设目录。
- ReadFile 先以 UTF-8 读取整个文件，再按 `offset`、`limit` 切片，默认最多返回 2000 行。
- ReadFile 的行数限制控制返回范围，当前实现没有按范围直接从磁盘读取。

### 4.3 为什么 Grep 搜过文件后，编辑前仍要 ReadFile

默认工具注册时，ReadFile、EditFile、WriteFile 共用一个 [FileStateCache](../mewcode/tools/file_state_cache.py)。

ReadFile 成功读取后，记录文件的绝对路径和修改时间。EditFile 编辑前检查：

1. 文件是否已经读过。
2. 文件是否在读后被其他操作修改过。

Grep 不会记录这个状态，因此搜索结果不能代替 ReadFile 的已读记录。这个保护机制依赖工具被注入共享缓存；单独创建 `ReadFile()` 时，缓存参数可以为空。

常见顺序：**Grep 定位 → ReadFile 看上下文 → EditFile 修改。**

## 5. ReadFile 如何把内容交给 LLM

**ReadFile 负责读取，LLM 负责理解，Agent 负责把两者连接起来。**

```text
LLM 决定查看某个文件，返回 ReadFile 调用
        ↓
Agent 查找工具并校验参数
        ↓
ReadFile 读取文件，返回带行号的 ToolResult
        ↓
Agent 将结果封装为 ToolResultBlock，加入对话历史
        ↓
serialization.py 转成当前模型 API 的消息格式
        ↓
client.py 再次请求模型
        ↓
LLM 分析内容，决定继续搜索、修改或回答
```

ReadFile 本身不会分析业务逻辑，也不会直接请求 LLM。它通常返回：

```python
ToolResult(output="带行号的文件内容", is_error=False)
```

模型只能看到被返回并实际发送的内容。如果只读了局部行，模型这一轮获得的文件上下文也只有这些行；需要更多内容时可以继续调用。

对于特别大的工具输出，Agent 层还可能执行溢写或截断，模型实际收到的内容受这些预算规则影响。

## 6. Python 工具如何转换成 JSON Schema

### 6.1 用 Pydantic 声明参数

ReadFile 的参数定义如下，字段说明为便于学习作了简化：

```python
from pydantic import BaseModel, Field


class Params(BaseModel):
    file_path: str = Field(description="要读取的文件路径")
    offset: int = Field(default=0, description="起始行偏移，从 0 开始")
    limit: int = Field(default=2000, description="最多读取多少行")
```

| Python 声明 | JSON Schema 中的信息 |
|---|---|
| `str` | `type: "string"`。 |
| `int` | `type: "integer"`。 |
| 没有默认值 | 列入必填参数 `required`。 |
| `default=0` | 默认值为 0。 |
| `Field(description=...)` | 字段用途说明。 |

### 6.2 通过 Tool.get_schema() 生成工具说明

[base.py](../mewcode/tools/base.py) 中的实现：

```python
def get_schema(self):
    schema = self.params_model.model_json_schema()
    schema.pop("title", None)
    return {
        "name": self.name,
        "description": self.description,
        "input_schema": schema,
    }
```

生成结果的结构如下，省略字段说明和字段标题：

```json
{
  "name": "ReadFile",
  "description": "Read a file and return its contents with line numbers.",
  "input_schema": {
    "type": "object",
    "properties": {
      "file_path": {"type": "string"},
      "offset": {"type": "integer", "default": 0},
      "limit": {"type": "integer", "default": 2000}
    },
    "required": ["file_path"]
  }
}
```

`input_schema` 中描述的是参数结构，外层还包含工具名称和用途。

### 6.3 注册表汇总，客户端发送

[ToolRegistry](../mewcode/tools/__init__.py) 按名称保存工具实例，`get_all_schemas(protocol)` 汇总可提供给模型的定义，并根据协议调整包装形式。

- Anthropic 使用 `name`、`description`、`input_schema`。
- Responses 使用 `type: "function"`、`name`、`description`、`parameters`。
- Chat Completions 还会在客户端中把名称、描述和参数嵌套进 `function`。

LLM 根据这些定义生成工具调用，Python 的 `execute()` 留在本地执行。

```text
Python 参数模型
    ↓ model_json_schema()
参数 JSON Schema
    ↓ 加上工具名称和说明，适配 API 协议
模型可用的工具定义
    ↓
LLM 返回工具名称和参数
    ↓ model_validate()
本地执行 execute()
```

**同一份 Pydantic 模型，既用来生成参数说明，也用来校验实际调用参数。** Schema 能指导模型填写，但不能保证模型一定填对，所以运行时校验仍然必要。

## 7. 工具的并发执行规则

### 7.1 为什么只读工具通常可以并发

ReadFile 和 Grep 不修改项目文件。在文件读取期间保持稳定、调用之间没有依赖的前提下，它们通常不会互相干扰。

Tool 基类默认按类别判断：

```python
@property
def is_read_only(self):
    return self.category == "read"


def is_concurrency_safe(self, args):
    return self.is_read_only
```

这里的“只读”主要指不修改项目或外部业务状态；ReadFile 仍会更新内部已读缓存。

### 7.2 为什么写工具默认串行

两个调用同时修改同一文件，可能发生覆盖：

```text
调用 A：读取旧内容 → 修改函数 A → 写回文件
调用 B：读取旧内容 → 修改函数 B → 写回文件
```

如果两者都基于旧内容修改，最后写回的版本可能丢失另一份修改。读取后修改时间检查能提供保护，但不能视为完整的并发锁。

即使操作不同文件，也可能有顺序依赖：

```text
WriteFile 生成 config.json
        ↓ 完成后
Bash 启动程序，读取 config.json
```

因此，当前调度策略将写工具默认串行执行。写操作在理论上可以通过依赖分析、锁等机制实现部分并发，本项目这里采用更简单的分类策略。

### 7.3 当前如何划分批次

[partition_tool_calls()](../mewcode/agent.py) 把相邻且判定为并发安全的调用归入同一批次，其余调用单独成批：

```text
调用顺序：ReadFile → Grep → EditFile → ReadFile → Grep

批次 1：ReadFile、Grep，并发执行
批次 2：EditFile，单独执行
批次 3：ReadFile、Grep，并发执行

批次按 1 → 2 → 3 的顺序执行。
```

`StreamingExecutor.execute_all()` 在批内使用：

```python
await asyncio.gather(
    *(run_one(tc) for tc in batch.calls),
    return_exceptions=True,
)
```

`gather()` 按传入顺序返回结果。执行器保留调用 ID，并按批次拼接结果。

实现范围需要注意：

- `StreamingExecutor` 会先收集调用，待模型流结束后按批执行。
- 需要交互式权限确认的调用会另外延迟执行，上述顺序保证针对执行器自己的批次。
- `run_to_completion()` 当前使用另一条路径，逐个执行本轮工具调用。
- `async def` 不自动带来真正重叠的 I/O；当前 ReadFile、Grep 内部使用同步文件操作，即使分在并发批次，也可能阻塞事件循环。

### 7.4 为什么 Bash 要根据命令判断

Bash 是通用命令执行入口，同一个工具既能读文件，也能改变项目状态：

| 命令示例 | 行为 | 当前判定 |
|---|---|---|
| `cat a.py` | 读取文件。 | 可并发。 |
| `git status` | 查看状态。 | 可并发。 |
| `mv a.py b.py` | 移动文件。 | 串行。 |
| `npm install` | 修改依赖文件和目录。 | 串行。 |
| `echo hello > a.py` | 覆盖文件。 | 串行。 |
| `ls | wc -l` | 只读管道。 | 当前统一按串行处理。 |

[Bash.is_concurrency_safe()](../mewcode/tools/bash.py) 覆写基类方法：

```python
def is_concurrency_safe(self, args):
    command = args.get("command")
    return isinstance(command, str) and is_safe_command(command)
```

[is_safe_command()](../mewcode/permissions/dangerous.py) 当前先排除 `|`、`;`、`&&`、`>`、`$(`、反引号等字符，再按安全命令前缀白名单判断。

**当前实现有误判，不能把判定为 True 理解成绝对只读。**

例如白名单中包含 `sed` 和 `tee`，又没有完整分析参数，因此 `sed -i ...`、`tee example.txt` 也能通过判断，但它们可以修改文件。这是源码中的已有局限，本笔记仅记录，没有修复。

## 8. 工具失败后如何让 Agent 继续运行

**把单次调用失败封装成错误 ToolResult，加入对话历史，交给 LLM 决定后续动作。**

### 8.1 三种常见错误

执行逻辑主要在 [agent.py](../mewcode/agent.py) 的 `_execute_single_tool_direct()`、`_execute_tool()` 和 `_execute_tool_noninteractive()` 中。

| 情况 | 检查方式 | 返回内容 |
|---|---|---|
| 工具不存在 | `registry.get(name)` 返回 `None`。 | `unknown tool`，`is_error=True`。 |
| 工具被禁用 | `registry.is_enabled(name)` 返回 `False`。 | `tool ... is disabled`，`is_error=True`。 |
| 参数校验失败 | `model_validate(arguments)` 抛出 `ValidationError`。 | 参数校验错误详情，`is_error=True`。 |
| 工具执行抛异常 | 捕获 `Exception`。 | 执行错误详情，`is_error=True`。 |

禁用工具仍保存在注册表中，所以“工具是否存在”和“工具是否启用”需要分别判断。禁用工具通常也不会被 `get_all_schemas()` 提供给模型，但运行时仍需检查。

### 8.2 简化实现

下面是机制示意，省略权限、Hook、计时和追踪等逻辑，并统一直接返回 ToolResult：

```python
from pydantic import ValidationError

from mewcode.tools.base import ToolResult


async def execute_tool(registry, tool_name, arguments):
    tool = registry.get(tool_name)

    if tool is None:
        return ToolResult(
            output=f"Error: unknown tool '{tool_name}'",
            is_error=True,
        )

    if not registry.is_enabled(tool_name):
        return ToolResult(
            output=f"Error: tool '{tool_name}' is disabled",
            is_error=True,
        )

    try:
        params = tool.params_model.model_validate(arguments)
        return await tool.execute(params)
    except ValidationError as e:
        return ToolResult(
            output=f"Parameter validation error: {e}",
            is_error=True,
        )
    except Exception as e:
        return ToolResult(
            output=f"Tool execution error: {e}",
            is_error=True,
        )
```

例如 ReadFile 调用缺少必填的 `file_path` 时，参数校验失败，`execute()` 不会被调用，模型会收到参数错误信息。

### 8.3 错误结果也必须保留调用 ID

Agent 将结果封装为内部消息块：

```python
ToolResultBlock(
    tool_use_id=tc.tool_id,
    content=result.output,
    is_error=result.is_error,
)
```

再通过 `conversation.add_tool_results_message(...)` 加入历史。

这里的调用 ID 是关键：即使执行失败，也需要向模型 API 返回与原调用对应的结果，避免工具调用与结果不配对。

批处理执行器还有额外兜底：并发时使用 `return_exceptions=True` 收集异常；串行时捕获普通异常；把失败转换为携带原调用 ID 的错误结果。

### 8.4 LLM 如何处理错误

```text
LLM 发起 ReadFile 调用，但遗漏 file_path
        ↓
Pydantic 校验失败
        ↓
生成 ToolResult(is_error=True, output="参数校验错误详情")
        ↓
结果携带原调用 ID，加入对话历史
        ↓
再次请求 LLM
        ↓
LLM 可以补齐参数重试、换工具或向用户解释问题
```

`is_error=True` 是单次工具调用的失败标记，不是直接终止 Agent 的指令。OpenAI 两种消息格式当前主要通过结果文本传递错误，Anthropic 还会发送显式的 `is_error` 字段。

这套处理覆盖上述工具调用错误；用户取消任务、模型 API 异常等仍属于其他控制流程。

## 9. 源码阅读顺序与复习要点

### 9.1 建议阅读顺序

1. [tools/base.py](../mewcode/tools/base.py)：理解 Tool、ToolResult、参数模型和 Schema。
2. [tools/read_file.py](../mewcode/tools/read_file.py)、[tools/grep.py](../mewcode/tools/grep.py)：看两个具体工具。
3. [tools/__init__.py](../mewcode/tools/__init__.py)：理解注册、启用、禁用和 Schema 汇总。
4. [conversation.py](../mewcode/conversation.py)、[serialization.py](../mewcode/serialization.py)：理解内部消息与 API 消息的转换。
5. [agent.py](../mewcode/agent.py)：重点找 `partition_tool_calls()`、`StreamingExecutor`、`_execute_single_tool_direct()` 和 `run_to_completion()`。
6. [tools/bash.py](../mewcode/tools/bash.py)、[permissions/dangerous.py](../mewcode/permissions/dangerous.py)：理解按具体命令判断的策略和局限。
7. [agents/loader.py](../mewcode/agents/loader.py)、[tools/agent_tool.py](../mewcode/tools/agent_tool.py)：理解子智能体如何创建。

### 9.2 已有测试可以看什么

| 测试文件 | 可学习内容 |
|---|---|
| [test_serialization.py](../tests/test_serialization.py) | 各协议消息结构、思考块和工具调用格式。 |
| [test_streaming_batching.py](../tests/test_streaming_batching.py) | 写调用不重叠、读调用分组、结果顺序、异常保留 ID、Bash 并发判断。 |
| [test_agent.py](../tests/test_agent.py) | 未知工具返回错误结果后，Agent 循环仍继续。 |
| [test_subagent.py](../tests/test_subagent.py) | 子智能体解析、加载、工具过滤、fork、后台任务和通知。 |

这里列出的是阅读入口，没有声明本次已运行这些测试。

### 9.3 一句话复习

- **agents/**：管子智能体角色及运行管理。
- **serialization.py**：把内部消息翻译成模型 API 格式。
- **Grep**：定位代码在哪里。
- **ReadFile**：返回代码上下文，供 LLM 理解。
- **JSON Schema**：告诉模型工具怎么用；Pydantic 再校验实际参数。
- **并发策略**：相邻安全调用一起执行，可能修改状态的调用默认串行。
- **Bash 判定**：看具体命令和参数；当前前缀白名单仍有误判。
- **错误处理**：保留调用 ID，把失败作为结果返回，让模型有机会调整下一步。
