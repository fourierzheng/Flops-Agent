---
name: project_understanding
description: "Explore and analyze the codebase to understand what the project does, its architecture, tech stack, and code organization. Use this whenever the user wants to learn about the project as a whole."
---

# 项目理解技能 (Project Understanding Skill)

## 目的

快速、全面地理解一个项目，让 AI 助手和人类都能高效地掌握项目的全貌。

## 使用说明

### 0. 检查已有文档

**首先**检查项目根目录是否已有 `AGENTS.md` 文件：

```bash
[ -f AGENTS.md ] && echo "found" || echo "not found"
```

- **如果存在**：直接读取 `AGENTS.md` 的内容作为项目理解的结果，跳过以下分析步骤
- **如果不存在**：继续执行以下分析步骤，**并在最后将结果保存到 `AGENTS.md`**

### 1. 分析项目结构

首先探索项目的目录结构：

```bash
# 查看根目录结构
ls -la

# 检测项目类型（检查配置文件是否存在）
[ -f package.json ] && echo "Node.js" || \
[ -f setup.py ] || [ -f pyproject.toml ] || [ -f requirements.txt ] && echo "Python" || \
[ -f Cargo.toml ] && echo "Rust" || \
[ -f go.mod ] && echo "Go" || \
[ -f pom.xml ] || [ -f build.gradle ] && echo "Java" || \
[ -f *.sln ] || [ -f *.csproj ] && echo "C#/.NET" || \
[ -f CMakeLists.txt ] || [ -f Makefile ] && echo "C/C++" || \
echo "Unknown"

# 查看项目配置文件内容
for f in package.json setup.py pyproject.toml Cargo.toml go.mod pom.xml build.gradle; do
  [ -f "$f" ] && echo "=== $f ===" && head -20 "$f"
done
```

### 2. 识别技术栈

检查关键配置文件确定技术栈：

- **Python**: `requirements.txt`, `setup.py`, `pyproject.toml`, `Pipfile`
- **Node.js**: `package.json`, `package-lock.json`
- **Java**: `pom.xml`, `build.gradle`
- **Go**: `go.mod`
- **Rust**: `Cargo.toml`
- **C/C++**: `CMakeLists.txt`, `Makefile`, `*.pro`
- **其他**: `.ruby-version`, `Gemfile`, `Dockerfile`, `Makefile`

### 3. 理解代码组织

分析代码目录结构：

```bash
# 查看目录树
find . -type d -maxdepth 3 -not -path '*/node_modules/*' -not -path '*/.git/*' -not -path '*/__pycache__/*' | head -50

# 检查主要的源代码目录
ls -la src/ 2>/dev/null || ls -la app/ 2>/dev/null || ls -la lib/ 2>/dev/null
```

### 4. 生成项目概览

整合以上信息，生成结构化的项目理解报告，包含：

1. **项目基本信息**
   - 项目名称
   - 项目类型/语言
   - 技术栈概览
   - 依赖管理方式

2. **目录结构说明**
   - 主要目录及用途
   - 源代码组织方式
   - 配置文件位置

3. **核心功能模块**
   - 主要模块/包列表
   - 入口文件位置
   - 关键功能说明

4. **特殊配置**
   - 环境变量要求
   - 特殊依赖或工具
   - 构建/运行方式

### 5. 输出格式

最终输出一个清晰的项目理解摘要，包含：

- 项目整体架构图（文字形式）
- 关键文件和目录的说明
- 技术栈详细信息
- 快速开始指南

## 注意事项

- 忽略常见的无关目录：`node_modules`, `.git`, `__pycache__`, `venv`, `.venv`, `dist`, `build`
- 优先查看 README.md 获取项目说明
- 注意识别测试文件和文档
- 检查 CI/CD 配置文件了解项目工作流

## 输出要求

当项目根目录不存在 `AGENTS.md` 时，**必须**将项目理解的结果保存到 `AGENTS.md` 文件中。内容包括：
- 项目基本信息
- 技术栈详情
- 目录结构说明
- 核心功能模块
- 快速开始指南
