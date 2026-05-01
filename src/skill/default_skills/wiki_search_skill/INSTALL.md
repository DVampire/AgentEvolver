# Wiki Search Skill 安装指南

## 前置条件

- Python 3.10+
- Go 1.24+（用于编译 MCP server，如果使用预编译二进制则不需要）

## 第一步：安装 Python 依赖

```bash
pip install mcp
```

这是唯一需要额外安装的 Python 包，用于通过 stdio 与 MCP server 通信。

## 第二步：编译 mediawiki-mcp-server（在repo中git commit了，应该不用重新下载了）

```bash
# 克隆源码
git clone https://github.com/olgasafonova/mediawiki-mcp-server.git /tmp/mediawiki-mcp-server

# 打 patch：允许匿名访问公开 wiki（如 Wikipedia）
# 原版要求所有操作都登录，但 Wikipedia 公开 API 不需要认证即可读取
# 修改 wiki/client.go 中的 login 函数，将：
#
#   if !c.config.HasCredentials() {
#       return fmt.Errorf("no credentials configured. Set MEDIAWIKI_USERNAME and MEDIAWIKI_PASSWORD environment variables")
#   }
#
# 替换为：
#
#   if !c.config.HasCredentials() {
#       c.loggedIn = true
#       c.tokenExpiry = time.Now().Add(24 * time.Hour)
#       c.logger.Info("No credentials configured, using anonymous access")
#       return nil
#   }

# 编译，输出到项目 bin 目录
cd /tmp/mediawiki-mcp-server
go build -o <AgentOS项目根目录>/bin/mediawiki-mcp-server .
```

编译完成后确认二进制文件存在：

```bash
ls -la bin/mediawiki-mcp-server
```

## 第三步：验证安装

```bash
# 进入 AgentOS 项目根目录，依次运行：

# 1. 确认 MCP server 可以启动
bin/mediawiki-mcp-server --help

# 2. 列出所有可用的 MCP tools（应返回 42 个 tools 的 JSON 列表）
WIKI_MCP_SERVER_PATH=$(pwd)/bin/mediawiki-mcp-server \
  python src/skill/default_skills/wiki_search_skill/scripts/wiki_search.py list-tools

# 3. 测试搜索
WIKI_MCP_SERVER_PATH=$(pwd)/bin/mediawiki-mcp-server \
  python src/skill/default_skills/wiki_search_skill/scripts/wiki_search.py search "quantum computing" --limit 3

# 4. 测试页面摘要
WIKI_MCP_SERVER_PATH=$(pwd)/bin/mediawiki-mcp-server \
  python src/skill/default_skills/wiki_search_skill/scripts/wiki_search.py summary "Quantum_computing"
```

如果以上命令都返回正常 JSON 数据，说明安装成功。

## 配置说明

默认配置在 `resources/config.json`：

```json
{
    "server_path": "bin/mediawiki-mcp-server",
    "mediawiki_url": "https://en.wikipedia.org/w/api.php"
}
```

也可以通过环境变量覆盖：

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `WIKI_MCP_SERVER_PATH` | MCP server 二进制路径 | `bin/mediawiki-mcp-server` |
| `MEDIAWIKI_URL` | MediaWiki API 地址 | `https://en.wikipedia.org/w/api.php` |

## 常见问题

**Q: 运行脚本时报 `ModuleNotFoundError: No module named 'mcp'`**
A: 执行 `pip install mcp`

**Q: 运行脚本时报 `FileNotFoundError` 或 `No such file or directory`**
A: MCP server 二进制路径不对。用 `WIKI_MCP_SERVER_PATH` 环境变量指定绝对路径，或确认 `bin/mediawiki-mcp-server` 存在。

**Q: 搜索时报 `no credentials configured`**
A: MCP server 没有打 patch。请按第二步中的说明修改 `wiki/client.go` 并重新编译。

**Q: 搜索时报 `login failed`**
A: 同上，说明设了 `MEDIAWIKI_USERNAME`/`MEDIAWIKI_PASSWORD` 环境变量但凭证无效。对于 Wikipedia 只读访问，不需要设这两个变量。
