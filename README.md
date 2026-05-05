# Flops Agent

一个基于 LLM 的智能 CLI 代理工具，支持多轮对话、文件操作、Shell 命令执行、Web 访问等工具调用能力。

## 特性

- 🤖 **LLM 驱动**：基于 Anthropic/OpenAI 兼容 API 的智能代理
- 🔧 **工具系统**：内置丰富的工具集，包括文件操作、Shell 执行、Web 搜索等
- 🛠️ **技能扩展**：支持自定义技能扩展特定领域能力
- 💬 **多轮对话**：支持多轮对话和上下文记忆
- 🎨 **彩色终端**：美观的 ANSI 彩色输出，带有 ASCII 艺术界面
- 📡 **流式输出**：实时流式响应展示（基于 Rich Live）
- 🔄 **对话压缩**：自动压缩历史对话，节省 token 消耗
- ↩️ **撤销支持**：支持撤销上一轮对话及其文件修改
- 🔧 **工具过滤**：按需启用/禁用特定工具

## 技术栈

- **语言**：Python 3.13+
- **依赖**：anthropic, openai, prompt_toolkit, pydantic, rich, httpx, pyyaml, bashlex
- **构建**：hatchling
- **包管理**：uv
- **测试**：pytest, pytest-asyncio

## 快速开始

### 安装

### 一键安装（推荐）

```bash
# 方式1: 直接 curl 安装
curl -fsSL https://raw.githubusercontent.com/fourierzheng/Flops-Agent/main/install.sh | bash

# 方式2: 克隆后本地安装
git clone https://github.com/fourierzheng/Flops-Agent.git
cd Flops-Agent
bash install.sh
```

安装脚本会自动：
- 检测并安装 Python 依赖
- 创建可执行脚本 `flops`，默认安装到 `~/.local/bin` 目录下，无需手动 PATH 配置

### 配置

安装后会自动创建配置文件 `~/.config/flops/config.json`，或编辑项目根目录下的 `config.json`。

配置使用多 Provider 模式，每个 Provider 下可配置多个模型：

```json
{
    "name": "flops",
    "providers": {
        "MiniMax": {
            "api_key": "YOUR-API-KEY-HERE",
            "base_url": "https://api.minimaxi.com/anthropic",
            "models": {
                "MiniMax-M2.7": {
                    "max_tokens": 8192
                }
            }
        },
        "DeepSeek": {
            "api_key": "YOUR-API-KEY-HERE",
            "base_url": "https://api.deepseek.com/",
            "models": {
                "deepseek-v4-flash": {
                    "max_tokens": 8192
                },
                "deepseek-v4-pro": {
                    "max_tokens": 8192
                }
            }
        },
        "Kimi": {
            "api_format": "anthropic",
            "api_key": "YOUR-API-KEY-HERE",
            "base_url": "https://api.kimi.com/coding/",
            "models": {
                "Kimi-K2.6": {
                    "max_tokens": 8192
                }
            }
        }
    },
    "agent": {
        "model": "DeepSeek:deepseek-v4-flash",
        "max_turns": 200
    },
    "log": {
        "level": "INFO"
    },
    "skills": {
        "paths": ["skills"]
    }
}
```

#### 配置字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 否 | 应用名称，默认为 `"flops"` |
| `providers` | object | 是 | 模型提供商配置，key 为提供商名称 |
| `agent.model` | string | 是 | 默认模型，格式 `"ProviderName:ModelName"` |
| `agent.max_turns` | int | 否 | 最大对话轮数，默认 200 |
| `log.level` | string | 否 | 日志级别：`DEBUG` / `INFO` / `WARNING` / `ERROR`，默认 `INFO` |
| `skills.paths` | string[] | 否 | 技能目录路径列表，默认 `["skills"]` |

**Provider 字段说明：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `api_key` | string | 是 | API 密钥 |
| `base_url` | string | 是 | API 请求地址 |
| `api_format` | string | 否 | API 协议格式：`"anthropic"` / `"openai"` / `"auto"`（自动检测）。不填则默认为 OpenAI 格式 |
| `models` | object | 是 | 模型列表，key 为模型名称，value 为模型参数 |

**Model 字段说明：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `max_tokens` | int | 是 | 最大输出 token 数 |
| `context_size` | int | 否 | 上下文窗口大小（仅用于展示信息） |
| `thinking` | bool | 否 | 是否支持 extended thinking（Anthropic 格式专用） |
| `request_timeout` | int | 否 | 请求超时时间（秒） |

**可选配置（应用启动后自动生成）：**

```json
{
    "memory": {
        "enabled": true,
        "distill_interval": 10
    }
}
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `memory.enabled` | bool | `true` | 是否启用长期记忆 |
| `memory.distill_interval` | int | `10` | 每 N 轮对话自动蒸馏记忆一次 |

> 模型引用格式为 `"ProviderName:ModelName"`（如 `"DeepSeek:deepseek-v4-flash"`）。以上示例为敏感信息脱敏后的真实结构。

### 运行

安装完成后，直接运行：

```bash
flops
```

## 使用方法

### 命令

- `/help` - 显示帮助信息
- `/history` - 显示对话历史
- `/clear` - 清除对话历史
- `/session` - 列出会话或恢复：`/session [session_id]`
- `/model` - 列出模型或切换：`/model [model_name]`
- `/compact` - 手动压缩对话历史
- `/remember` - 手动触发记忆蒸馏
- `/skills` - 列出可用技能
- `/undo` - 撤销上一轮对话（回退对话和文件修改）
- `/init` - 初始化项目并生成 `AGENTS.md` 文件
- `/exit` - 退出程序

### 快捷键

- `Ctrl+C` - 中断当前响应
- `Ctrl+D` - 退出程序

## 工具系统

Flops Agent 提供以下内置工具：

| 工具 | 功能 |
|------|------|
| `agent` | 委托任务给子代理 |
| `fileread` | 读取文件内容 |
| `filewrite` | 写入文件 |
| `fileedit` | 编辑文件（替换字符串） |
| `shell` | 执行 Shell 命令（含安全检查） |
| `web` | 获取网页内容 |
| `grep` | 搜索文件内容 |
| `glob` | 使用 glob 模式匹配文件 |
| `list` | 列出目录内容 |
| `mem` | 查询长期记忆 |
| `python` | 执行 Python 代码 |
| `skill` | 调用技能 |
| `rm` | 删除文件或空目录 |
| `weather` | 查询天气 |

## 记忆系统

Flops Agent 具备长期记忆能力，通过 SQLite 存储和 FLOPS.md 持久化事实。

- **自动蒸馏**：每 N 轮对话自动从历史中提取关键事实（`distill_interval` 控制频率）
- **置信度机制**：事实有 1-5 的置信度评分，`mode="auto"` 增量更新，`mode="confirm"` 设为 5
- **promotion**：高置信度（>=3）的事实自动写入 FLOPS.md 作为持久化 Charter
- **手动触发**：`/remember` 命令立即触发一次蒸馏
- **`mem` 工具**：LLM 可通过 `mem` 工具查询记忆，回顾用户偏好和项目决策

## 技能系统

技能系统允许扩展特定领域的专业能力。技能定义在 `skills/` 目录下。

### 内置技能

- `test_skill` - 测试技能示例
- `project_understanding` - 项目理解技能

### 创建新技能

1. 在 `skills/` 目录下创建新技能文件夹
2. 编写技能定义文件（参考现有技能）
3. 在技能中使用 `skill` 工具调用

## 开发

### 开发运行

项目要求 Python 3.13+，建议使用 [uv](https://docs.astral.sh/uv/) 管理环境和依赖：

```bash
# 1. 确保使用正确 Python 版本（uv 会自动下载）
uv python pin 3.13

# 2. 创建虚拟环境并安装依赖
uv sync

# 3. 直接运行（不需要全局安装）
uv run python -m flops

# 4. 指定配置文件运行
uv run python -m flops --config /path/to/config.json
```

如果你还没有 uv：
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 运行测试

```bash
# 运行全部测试
pytest tests/

# 运行单个测试文件
pytest tests/test_agent.py -v

# 详细输出
pytest -v tests/
```

## License

MIT
# Flops-Agent
