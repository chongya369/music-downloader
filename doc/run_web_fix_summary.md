# run_web.bat 启动失败排查总结

## 现象

运行 `run_web.bat` 后服务未启动，脚本静默退出（或仅提示“按任意键继续”），Web 服务无法访问。

## 排查过程

1. **排除端口冲突**：应用监听端口 `56700` 无任何进程占用，系统亦无残留 python/ncm 进程，端口空闲。
2. **确认应用正常**：直接运行 `.venv\Scripts\python.exe webapp\app.py`，服务可正常启动并监听 `56700`。
3. **定位到脚本层**：应用本身无问题，判定 `run_web.bat` 在启动 python 前提前退出。通过逐步骤添加 `[STEP]` 日志标记、去除 `pause`，定位到脚本中止于 Python 版本判断处。

## 根因

### cmd 解析器缺陷

脚本先用 `for /f` 经命令替换获取 Python 版本：

```batch
for /f "delims=" %%i in ('%PYTHON_CMD% -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set PY_VERSION=%%i
```

其后紧跟多行 `if ( )` 块做版本校验：

```batch
if %PY_MAJOR% LSS 3 (
    echo [ERRO] Python version too old: %PY_VERSION% (need 3.10+)
    exit /b 1
)
```

两者组合触发 Windows cmd 解析器 bug：**经 `for /f` 命令替换取得的变量，其紧随的多行 `if ( )` 块会导致脚本静默退出（退出码 1），且不打印任何错误。**

### 验证

| 测试 | 结构 | 结果 |
|------|------|------|
| 普通 `set` + 多行 `if` | 无命令替换 | 通过 |
| `for /f` 字符串取值 + 多行 `if` | 无命令替换 | 通过 |
| `for /f` 命令替换 + 多行 `if` | 触发 bug | 失败（静默退出 1） |
| `for /f` 命令替换 + 单行 `if` | 改单行判断 | 通过 |

> 附注：脚本曾误写为 LF 行尾。Windows 批处理严格要求 **CRLF** 行尾，否则多行 `if ( )` 块解析同样可能出错，已一并修正。

## 修复方案

将版本校验由多行 `if ( )` 块改为**单行 `if` + 标记变量**，规避解析器 bug：

```batch
set /a PY_VER_ERR=0
if %PY_MAJOR% LSS 3 set PY_VER_ERR=1
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 set PY_VER_ERR=1
if %PY_VER_ERR% EQU 1 echo [ERRO] Python version too old: %PY_VERSION% (need 3.10+)
if %PY_VER_ERR% EQU 1 exit /b 1
echo [INFO] Python %PY_VERSION% found
```

其余判断（venv/pip、依赖、API 二进制）所用变量来自外部命令 `errorlevel` 或普通赋值，非 `for /f` 命令替换，不触发该 bug，故保持原结构。

## 验证结果

修复后重跑 `run_web.bat`：

- 所有 `[STEP]` 标记完整走完；
- 成功启动 Flask 服务；
- 端口 `56700` 正常 `LISTENING`。

服务可正常访问。

## 预防建议

- 批处理文件必须使用 **CRLF** 行尾，勿用 LF。
- 避免在 `for /f ... in ('...命令替换...')` 之后紧跟多行 `if ( )` 块；如需判断，优先采用单行 `if` + 标记变量的写法。
- `echo` 文本中避免出现不配对括号，以免干扰 `if ( )` 块的括号配对。