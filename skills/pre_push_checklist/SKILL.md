---
name: pre_push_checklist
description: 提交前格式化、编译检查、review、提交推送标准流程
---

# 代码提交工作流

修改完代码后，按以下步骤完成格式化、检查、提交和推送。

## 步骤

### 1. 检查当前状态

```bash
git status          # 看当前工作区状态，确认改了什么文件
git diff            # 看具体改动，确保没改到无关内容
```

### 2. 格式化

根据项目语言选择格式化工具。

| 语言 | 工具 |
|------|------|
| Python | `black src/ tests/` |
| C/C++ | `clang-format -i src/**/*.cpp src/**/*.h` |
| Go | `gofmt -w .` |
| Rust | `cargo fmt` |
| JavaScript/TypeScript | `npx prettier --write .` |
| Java | `mvn spotless:apply` |

### 3. 编译检查

根据项目语言验证无语法错误：

```bash
# Python
.venv/bin/python -m py_compile src/xxx.py

# C/C++
make 或 cmake --build build

# Rust
cargo check

# Go
go build ./...

# TypeScript
npx tsc --noEmit
```

### 4. 逻辑验证

跑关键路径冒烟测试（如有），例如：

- 配置文件解析
- 新增核心函数 import 并返回预期结果
- 权限/校验逻辑是否正确

### 5. 跑测试

```bash
# Python
.venv/bin/python -m pytest tests/ -x -q

# C++
ctest --test-dir build

# Rust
cargo test

# Go
go test ./...

# JavaScript
npm test
```

### 6. Review diff

```bash
git diff
```

逐行检查，关注：

- 死代码（永远走不到的分支/校验）
- 忘加的 import/include
- 语义/行为改变是否符合预期
- 错误信息是否对用户友好

### 7. 检查文档

新增配置项、接口或字段时，同步更新相关文档。

### 8. Stage + 二次确认

```bash
git add -A
git status          # 确认没有意外文件（.swp、__pycache__、*.o 等）
```

### 9. 提交

```bash
git commit -m "type: description"
```

### 10. 确认 commits

```bash
git log --oneline -3   # 确认 commit 数量和 message 正确
```

### 11. 推送

```bash
git push
```
