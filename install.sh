#!/bin/bash
set -e

# Flops Agent 安装脚本
# 使用方式:
#   本地开发模式（项目目录）: bash install.sh
#   远程安装模式: curl -sSL https://raw.githubusercontent.com/linhaizhengdev/Flops-Agent/main/install.sh | bash

echo "==> Flops Agent 安装脚本"

REQUIRED_PYTHON="3.14"

# 检查或安装 uv
if command -v uv &> /dev/null; then
    echo "==> 找到 uv $(uv --version)"
else
    echo "==> 正在安装 uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# 检查或安装 Python
if ! uv python list | grep -q "$REQUIRED_PYTHON"; then
    echo "==> 安装 Python $REQUIRED_PYTHON..."
    uv python install "$REQUIRED_PYTHON"
fi
echo "==> Python 版本: $REQUIRED_PYTHON"

# 用户配置目录
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/flops"
SKILLS_DIR="$CONFIG_DIR/skills"
mkdir -p "$CONFIG_DIR"
mkdir -p "$SKILLS_DIR"

# 检测当前目录是否为本项目
CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IS_FLops_REPO=false
if [ -f "$CURRENT_DIR/pyproject.toml" ] && grep -q "name = \"flops\"" "$CURRENT_DIR/pyproject.toml" 2>/dev/null; then
    IS_FLops_REPO=true
fi

if [ "$IS_FLops_REPO" = true ]; then
    # 本地开发模式
    echo "==> 本地开发模式"
    PROJECT_DIR="$CURRENT_DIR"
else
    # 远程安装模式
    PROJECT_DIR="${INSTALL_DIR:-$HOME/.local/share/flops}"
    if [ ! -d "$PROJECT_DIR" ]; then
        echo "==> 克隆 Flops-Agent 仓库..."
        git clone https://github.com/linhaizhengdev/Flops-Agent.git "$PROJECT_DIR"
    else
        echo "==> 更新 Flops-Agent 仓库..."
        cd "$PROJECT_DIR" && git pull
    fi
fi

VENV_DIR="$PROJECT_DIR/.venv"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

echo "==> 项目目录: $PROJECT_DIR"
echo "==> bin 目录: $BIN_DIR"

# 创建虚拟环境
if [ ! -d "$VENV_DIR" ]; then
    echo "==> 创建虚拟环境..."
    uv venv "$VENV_DIR" --python "$REQUIRED_PYTHON"
fi

# 安装依赖和项目
echo "==> 安装 Python 依赖..."
cd "$PROJECT_DIR"
uv pip install --quiet -e .

# 创建可执行文件软链接
echo "==> 创建 flops 命令..."
mkdir -p "$BIN_DIR"
ln -sf "$VENV_DIR/bin/flops" "$BIN_DIR/flops"

# 确保 PATH 包含 bin 目录
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    SHELL_RC="${HOME}/.bashrc"
    if [ "$(uname)" = "Darwin" ]; then
        SHELL_RC="${HOME}/.zshrc"
    fi
    if ! grep -q "$BIN_DIR" "$SHELL_RC" 2>/dev/null; then
        echo "" >> "$SHELL_RC"
        echo "export PATH=\"$BIN_DIR:\$PATH\"" >> "$SHELL_RC"
    fi
    echo ""
    echo "==> 请重新加载 shell 或执行: export PATH=\"$BIN_DIR:\$PATH\""
fi

# 安装内置 skills
echo "==> 安装内置 skills..."
BUILTIN_SKILLS_DIR="$PROJECT_DIR/skills"
if [ -d "$BUILTIN_SKILLS_DIR" ]; then
    for skill_dir in "$BUILTIN_SKILLS_DIR"/*/; do
        skill_name="$(basename "$skill_dir")"
        target="$SKILLS_DIR/$skill_name"
        if [ -d "$target" ]; then
            echo "    skill '$skill_name' 已存在，跳过"
        else
            cp -r "$skill_dir" "$target"
            echo "    安装 skill: $skill_name"
        fi
    done
fi

# 创建配置文件
CONFIG_FILE="$CONFIG_DIR/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "==> 创建配置文件: $CONFIG_FILE"
    cat > "$CONFIG_FILE" << 'EOF'
{
    "name": "flops",
    "providers": {
        "example": {
            "api_key": "YOUR-API-KEY-HERE",
            "base_url": "https://api.minimaxi.com/anthropic",
            "api_format": "anthropic",
            "models": {
                "default": {
                    "max_tokens": 8192,
                    "context_size": 200000
                }
            }
        }
    },
    "agent": {
        "model": "example:default",
        "max_turns": 200
    },
    "log": {
        "level": "INFO"
    },
    "skills": {
        "paths": []
    },
    "tool": {
        "permission": "standard"
    }
}
EOF
    echo "==> 请编辑 $CONFIG_FILE 填入你的 API Key 并配置 provider"
fi

echo ""
echo "==> 安装完成！运行 'flops' 即可启动"
echo ""

