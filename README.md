# Blog Access

一个基于 Playwright 的博客访问脚本。脚本访问首页、从带日期路径的文章中选择一篇访问并返回首页，可选代理和 Telegram 统计通知。

## 代码结构

项目保持单入口，避免为小型脚本增加不必要的包层级：

- `Settings`：集中读取并校验环境变量；Token 和 Chat ID 不出现在对象表示中。
- `VisitStats`：显式持有本次运行的访问统计，不使用模块级可变字典。
- `ProxyEndpoint`：解析和编码代理凭据；日志只输出协议、主机和端口。
- `run_playwright()`：编排浏览器、首页、文章和通知流程，并确保浏览器最终关闭。

## 安装

需要 Python 3.10 或更高版本。

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## 配置

复制无秘密模板，然后只在本地填写：

```bash
cp .env.example .env
```

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `HOMEPAGE_URL` | 否 | 博客首页，默认使用脚本内公开地址；只接受不含用户名和密码的 HTTP(S) URL。 |
| `BOT_TOKEN` | 否 | Telegram Bot Token；与 `CHAT_ID` 同时提供才发送通知。 |
| `CHAT_ID` | 否 | Telegram Chat ID。 |
| `PROXY_FILE` | 否 | 代理列表路径，默认 `proxies.txt`。 |

代理文件每行格式：

```text
proxy.example.invalid:8080:http|username:password
```

密码可以包含冒号。代理文件只在本地保存，不得提交。

## 运行与测试

```bash
python AutoVisitV3.py
python -m unittest -v
```

## 敏感数据安全

- `.env`、`.env.*`、`proxies*.txt`、日志、私钥和常见证书文件均由 `.gitignore` 排除。
- `.env.example` 只包含变量名和无秘密示例，允许提交。
- 不要使用 `git add -f` 强制加入 `.env`、代理列表、Token、密码、私钥或运行日志。
- CI/CD 中使用 GitHub Actions Secrets，不要把秘密写入 workflow、命令参数、测试 fixture 或截图。
- 日志不得记录完整代理 URL、Telegram 请求 URL、请求参数或异常正文；当前实现只记录安全地址、状态码和异常类型。
- 如果秘密曾经提交，应立即轮换；清理 Git 历史需要单独授权和协作，不能只删除当前文件。

运行前请确认目标站点允许自动访问，并遵守其服务条款和访问频率限制。
