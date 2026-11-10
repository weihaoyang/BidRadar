# BidRadar · 招投标数据获取（CLI + MCP）

中国 + 海外公开招投标数据的统一入口。一个 SQLite 库、一套字段、一个 CLI、一个 MCP Server，覆盖：

- **实时搜索**：按关键词跨源检索
- **每日增量**：定时抓新增标讯
- **准实时监控**：轮询 + 关键词/地区/金额过滤 + Webhook 推送
- **一次性全量导出**：CSV / JSON / JSONL / Excel
- **MCP 接入**：OpenCode、Claude Desktop、Cursor 等直接用自然语言查询

> 数据只来自公开页面/公开接口。请遵守目标站点 robots.txt、服务条款和当地法律，控制请求频率，不要用于转售或绕过访问控制。

---

## 1. 先看结论：哪些能直接用，哪些需要薄封装

调研后的现实情况：

| 能力 | 现成开源方案 | 本项目的处理 |
|---|---|---|
| 海外招标（EU/DE/UK/CH/CA/US） | 已有多个 MCP：`ausschreibungsagenten-mcp`、`tenderapi-mcp`、`simap-mcp`、`mcp-canada-tenders`、`tender-mcp`、`GovRider` | 可直接接入；本项目内置 TED（欧盟）和 SAM（美国）两个官方 API 连接器，减少外部依赖 |
| 全网搜索/网页抓取 | `SearXNG` + `mcp-searxng`、`Crawl4AI`（自带 MCP）、`Scrapling`、`Firecrawl`、`Crawlee` | 本项目不重复造爬虫；需要抓 JS 站点时接 Crawl4AI/Scrapling。见第 8 节 |
| 页面变更监控/告警 | `changedetection.io`（Apache-2.0，Docker，85+ 通知渠道） | 复杂页面监控可直接用；本项目内置轻量关键词轮询 + Webhook |
| 中国全国招投标聚合 | **没有找到可直接用的成熟 MCP**；多为单站点爬虫脚本（`TenderCrawler`、`CrawlerForCCGP` 等） | 本项目做薄封装：全国 + 省级公开接口 + 统一字段 |
| 中国政府采购 | 单站点爬虫较多，但限流严格 | 内置 ccgp 连接器（默认关闭），开启后 5 秒间隔；被限流时自动跳过并告警 |

因此，"一行代码都不写"在海外侧基本成立；中国侧目前没有现成 MCP，必须有一层薄封装。本项目的中国连接器只做 **HTTP 请求 + 字段映射**，爬虫/浏览器/反爬交给成熟开源项目。

### 调研过的开源项目

| 项目 | 许可 | 能解决什么 | 为什么没有直接整套采用 |
|---|---|---|---|
| [ir-st/TenderCrawler](https://github.com/ir-st/TenderCrawler) | MIT | cebpub + ccgp，CLI/Web/定时，快照对比 | 最接近中国需求，但没有 ggzy、没有 MCP、没有统一 SQLite/导出；cebpub 只抓各组合首页 |
| [ABoeotian/CrawlerForCCGP](https://github.com/ABoeotian/CrawlerForCCGP) | - | 中国政府采购网公告 | 单站点脚本，无统一字段/调度/MCP |
| [doyus/GBS_BidScope](https://github.com/doyus/GBS_BidScope) | - | 多源采集 + 浏览 + 分析 | 偏应用系统，接入改造成本高 |
| [unclecode/crawl4ai](https://github.com/unclecode/crawl4ai) | Apache-2.0 | JS 页面、反爬、Markdown/结构化提取，自带 MCP | 通用爬虫，不含标讯字段和去重/监控逻辑 |
| [D4Vinci/Scrapling](https://github.com/D4Vinci/Scrapling) | BSD-3 | 自适应选择器、 stealth 浏览器、MCP | 同上，作为抓取层复用 |
| [apify/crawlee](https://github.com/apify/crawlee) | Apache-2.0 | 高并发爬虫、代理轮换、断点续爬 | Node 生态，适合大规模站点扩展 |
| [searxng/searxng](https://github.com/searxng/searxng) + [mcp-searxng](https://github.com/ihor-sokoliuk/mcp-searxng) | AGPL/MIT | 自建全网搜索，无 API Key | 只做发现，不产出结构化标讯 |
| [dgtlmoon/changedetection.io](https://github.com/dgtlmoon/changedetection.io) | Apache-2.0 | 页面变更监控、关键词触发、85+ 通知 | 按页面差异告警，不按标讯字段入库 |
| [ausschreibungsagenten-mcp](https://github.com/shufflethis/ausschreibungsagenten-mcp) | MIT | 德国/欧盟/英国 17 源招标 MCP | 海外可直接用；中国不覆盖 |
| [tenderapi-mcp](https://github.com/idnsidns/tenderapi-mcp) | MIT | 法国 BOAMP + EU TED MCP | 同上 |
| [simap-mcp](https://github.com/Digilac/simap-mcp) | MIT | 瑞士 simap.ch MCP | 同上 |
| [mcp-canada-tenders](https://github.com/pipeworx-io/mcp-canada-tenders) | MIT | 加拿大 CanadaBuys MCP | 同上 |
| [GovRider](https://github.com/carlosahumada89/govrider-mcp-server) | MIT | 25+ 官方源匹配 | credits 限制，且中国不覆盖 |

结论：**海外用现成 MCP，中国用本项目薄封装，抓取层按需接 Crawl4AI/Scrapling/SearXNG。** 这样既不重复造轮子，又能把中国标讯统一进一个库。

---

## 2. 已实现的数据源

| ID | 名称 | 接入方式 | 覆盖 | 备注 |
|---|---|---|---|---|
| `cebpub` | 中国招标投标公共服务平台 | 公开 JSON 接口 `getStringMethod.do` | 全国招标公告、中标公告、评标公示、开标记录；央企 + 省市交易平台 | 实测稳定，无需登录；详情页有 JS 加密，先存列表字段 |
| `ggzy` | 全国公共资源交易平台 | 公开 JSON 接口 `getTradList` | 工程建设、政府采购、土地/矿业/产权等；2019 年至今 | 实测稳定；高频会触发验证码，需控制频率 |
| `ccgp` | 中国政府采购网 | HTML 检索 `bxsearch` | 政府采购公告、中标公告、更正公告 | 限流严格，默认关闭；开启后 5 秒间隔，失败会跳过 |
| `ted` | EU TED | 官方 REST API `POST /v3/notices/search` | 欧盟及欧洲经济区招标 | 无需 Key |
| `sam` | US SAM.gov | 官方 REST API `opportunities/v2/search` | 美国联邦合同机会 | 需要免费 `SAM_API_KEY` |
| `searxng` | SearXNG（可选） | 自建实例 JSON API | 全网发现/补充未接入的站点 | 需要 `SEARXNG_URL`，默认关闭 |
| `gd` | 广东省公共资源交易平台 | `search/v2/items` JSON 接口 | 广东全省交易公告 | YAML 配置：`sites/gd.yaml` |
| `js` | 江苏省公共资源交易平台 | `inteligentsearch` JSON 接口 | 江苏全省交易公告 | YAML 配置：`sites/js.yaml` |
| `zj` | 浙江省公共资源交易服务平台 | `inteligentsearch` JSON 接口 | 浙江全省交易公告 | YAML 配置：`sites/zj.yaml` |

统一字段：`source / external_id / title / publish_date / deadline / region / buyer / agency / category / amount / currency / url / summary / content / attachments / raw`。

---

## 3. 快速开始

要求：Python 3.11+（推荐 3.12/3.13），Windows / macOS / Linux。

```powershell
cd D:\投标数据获取

# 1) 安装（uv 已在本机可用）
uv venv
uv pip install -e .

# 2) 初始化配置
Copy-Item config.example.yaml config.yaml

# 2.1) 可选：填写 API Key / Crawl4AI / SearXNG
Copy-Item .env.example .env

# 3) 连通性自检
uv run bidradar doctor

# 4) 实时搜索
uv run bidradar search "信息化" -d 7 -n 20

# 5) 每日增量（读取 config.yaml 里的 keywords）
uv run bidradar sync -d 1

# 5.1) 历史回填（一次性全量导出前使用；按月切片）
uv run bidradar backfill -k "信息化" --start 2025-01-01 --end 2025-12-31 --max-pages 50

# 6) 导出 Excel
uv run bidradar export -f xlsx -o exports/信息化标讯.xlsx
```

如果不想用 `uv run`，激活虚拟环境后直接：

```powershell
.\.venv\Scripts\Activate.ps1
bidradar doctor
```

---

## 4. CLI 命令

```text
bidradar init                         初始化 config.yaml + SQLite
bidradar sources                      查看数据源和启用状态
bidradar doctor                       逐源连通性测试
bidradar search "关键词" -d 7 -n 50    实时搜索（可加 --json / --save）
bidradar sync -d 1                    增量抓取配置关键词
bidradar backfill -k "关键词" --start 2025-01-01 --end 2025-12-31  历史回填
bidradar export -f xlsx               导出本地库
bidradar detail <id>                  抓取单条详情（采购单位/预算/正文/附件）
bidradar enrich -s cebpub -n 20       批量补全详情
bidradar stats                        本地库统计
bidradar watch add ...                新增监控规则
bidradar watch list                   查看监控规则
bidradar watch run                    执行监控并推送
bidradar daemon --interval 300        准实时轮询（同步 + 监控）
bidradar mcp                          启动 MCP Server（stdio）
```

### 监控示例

```powershell
# 关键词 + 地区 + 金额阈值 + 排除词
uv run bidradar watch add 广东信息化 `
  --keyword "信息化,网络安全" `
  --region 广东 `
  --min-amount 1000000 `
  --exclude "废标,终止"

# 设置飞书机器人 Webhook
# config.yaml -> notify.webhook: https://open.feishu.cn/open-apis/bot/v2/hook/xxxx

# 手动跑一次
uv run bidradar watch run

# 每 5 分钟轮询
uv run bidradar daemon --interval 300
```

### 每日定时（Windows 任务计划）

```powershell
schtasks /Create /TN "BidRadar Daily" `
  /TR "D:\投标数据获取\.venv\Scripts\bidradar.exe sync -d 1" `
  /SC DAILY /ST 08:00
```

Linux/macOS 用 cron：

```cron
0 8 * * * cd /path/to/bidradar && .venv/bin/bidradar sync -d 1
```

---

## 5. 接入 OpenCode（MCP）

先确保已安装：

```powershell
cd D:\投标数据获取
uv venv
uv pip install -e .
```

在项目或全局 `opencode.json` 中加入（也可以直接把本项目的 `opencode.example.json` 复制为 `opencode.json` 后按需合并）：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "bidradar": {
      "type": "local",
      "command": [
        "D:/投标数据获取/.venv/Scripts/python.exe",
        "-m",
        "bidradar.mcp_server"
      ],
      "enabled": true,
      "environment": {
        "BIDRADAR_CONFIG": "D:/投标数据获取/config.yaml"
      }
    }
  }
}
```

重启 OpenCode 后，可以直接说：

- "用 bidradar 搜一下最近 7 天广东的信息化招标"
- "把关键词设为网络安全，每天帮我盯着，金额 100 万以上"
- "把本地库导出成 Excel"

### MCP 工具

| 工具 | 作用 |
|---|---|
| `search_tenders` | 实时跨源搜索 |
| `sync_tenders` | 增量同步到本地库 |
| `get_tender` | 按 ID 读完整字段；`fetch_detail=true` 时实时补全详情 |
| `enrich_tenders` | 批量补全采购单位/预算/正文/附件 |
| `list_sources` | 列出数据源 |
| `export_tenders` | 导出 CSV/JSON/Excel |
| `add_watch` / `list_watches` | 管理监控规则 |
| `run_watches` | 执行规则并可选推送 |
| `get_stats` | 本地库统计 |

---

## 6. 接入 Claude Desktop / Cursor

Claude Desktop 的 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "bidradar": {
      "command": "D:/投标数据获取/.venv/Scripts/python.exe",
      "args": ["-m", "bidradar.mcp_server"],
      "env": {
        "BIDRADAR_CONFIG": "D:/投标数据获取/config.yaml"
      }
    }
  }
}
```

Cursor 的 MCP 配置同理。

---

## 7. 数据源实测说明与限制

1. **cebpub**：列表接口稳定，返回 `businessId`、标题、发布时间、地区、行业、交易平台、公告结束时间。平台规则是"每个关键词 × 业务类型的新会话首次查询免 VAPTCHA"，所以本项目每个组合都新建会话、只取第一页（50 条）；翻页需要验证码。详情页是 JS + `findDetails.do` 接口，必须带 VAPTCHA 生成的 token，**建议用 Crawl4AI 浏览器渲染**；`bidradar detail/enrich` 已支持。
2. **ggzy**：一次请求即可拿到全国聚合结果，是最有"全网"性价比的入口。支持近一年内的分页查询（`DEAL_TIME=06` + 日期区间）和一年以前的历史接口（`/his/`），因此 **历史回填/全量导出优先用它**。单次查询总量上限约 1000 条，`backfill` 会按自然月自动切片。高频请求会返回 `code=800/829`（限流/验证码），代码会抛错并继续其它源。建议单 IP 每分钟不超过 10~20 次。
3. **ccgp**：`search.ccgp.gov.cn` 对 IP 限流很敏感，实测连续请求会直接返回"访问过于频繁"。默认关闭；开启后 5 秒间隔仍可能被限。**详情页 `www.ccgp.gov.cn/cggg/...` 可直连解析**，已验证能提取采购单位、代理机构、预算金额、截止时间、正文；详情抓取会用简单 UA 避免 WAF 403。
4. **gd / js / zj**：均为 JSON 接口，返回标题、地区、采购单位、发布时间、正文等字段。江苏/浙江的 `content` 字段直接带正文；广东详情页是 SPA，需要 Crawl4AI 渲染才能拿到正文。
5. **TED**：官方 API 无需 Key，支持分页和滚动模式；本项目按发布时间做客户端过滤。
6. **SAM**：需要在 [sam.gov](https://sam.gov) 免费申请 API Key，然后设置环境变量 `SAM_API_KEY`。
7. **合规**：所有连接器只访问公开页面/公开接口，不登录、不绕过付费墙。请自行确认目标站点条款和当地法律；不要高频压站。

---

## 8. 需要更强抓取能力时：直接接成熟开源项目

本项目刻意不重复实现浏览器池、验证码、代理轮换、页面变更检测。需要时按下面组合：

### 8.1 全网搜索（SearXNG）

```powershell
docker run -d --name searxng -p 8080:8080 `
  -v ${PWD}/searxng:/etc/searxng `
  searxng/searxng
```

然后接 `mcp-searxng`（Node，无需 API Key）：

```json
{
  "mcp": {
    "searxng": {
      "type": "local",
      "command": ["npx", "-y", "mcp-searxng"],
      "environment": { "SEARXNG_URL": "http://localhost:8080" }
    }
  }
}
```

用途：用 `site:ccgp.gov.cn 信息化 招标`、`site:ggzy.gov.cn ...` 做补充发现；或抓取未纳入连接器的省级平台。

### 8.2 JS 页面 / 反爬页面（Crawl4AI）

```powershell
docker run -d --name crawl4ai -p 11235:11235 `
  -e CRAWL4AI_API_TOKEN=change-me `
  unclecode/crawl4ai:latest
```

设置环境变量后，`bidradar detail/enrich` 会自动用 Crawl4AI 渲染 cebpub 详情页：

```powershell
$env:CRAWL4AI_URL="http://localhost:11235"
$env:CRAWL4AI_API_TOKEN="change-me"

# 单条
bidradar detail cebpub:xxxx

# 批量（从本地库挑缺正文/采购单位/金额的记录）
bidradar enrich -s cebpub -n 10
```

MCP 端点（可选）：`http://localhost:11235/mcp/sse`。适合 cebpub 详情页、广东 SPA 详情页、需要 JS 渲染的省级交易中心。

### 8.3 页面变更监控（changedetection.io）

```powershell
docker run -d --name changedetection -p 5000:5000 `
  -v ${PWD}/changedetection:/datastore `
  dgtlmoon/changedetection.io
```

适合"某个采购单位页面一有新公告就通知"；支持关键词触发、CSS/XPath 过滤、85+ 通知渠道。和本项目的区别：本项目按标讯字段入库、可检索、可导出；changedetection 按页面差异告警。

### 8.4 现成海外招标 MCP（可选）

- `ausschreibungsagenten-mcp`：德国/欧盟/英国 17 个源，匿名可用
- `tenderapi-mcp`：法国 BOAMP + EU TED，托管版免 Key
- `simap-mcp`：瑞士 simap.ch
- `mcp-canada-tenders`：加拿大 CanadaBuys
- `tender-mcp`：英国/欧盟/美国
- `GovRider`：25+ 官方源（有 credits 限制）

这些可以与本项目并存：海外交给专用 MCP，中国和统一入库交给 BidRadar。

### 8.5 新增省级站点（只改 YAML）

`sites/*.yaml` 是省级站点注册表，支持两种模式：

- `type: json`：请求 JSON 接口，用 dot-path 取列表和字段（广东/江苏/浙江已经这样接入）
- `type: html`：请求 HTML 列表页，用 CSS 选择器取字段（适合没有 JSON 接口的站点；可加 `render: true` 走 Crawl4AI）

一个 JSON 站点最小配置：

```yaml
id: gd
name: 广东省公共资源交易平台
type: json
method: POST
url: https://ygp.gdzwfw.gov.cn/ggzy-portal/search/v2/items
headers:
  Referer: https://ygp.gdzwfw.gov.cn/
page_start: 1
page_size: 20
body:
  pageNo: "${page}"
  pageSize: "${page_size}"
  keyword: "${keyword}"
  publishStartTime: "${start_compact}"
  publishEndTime: "${end_compact}"
list_path: data.pageData
total_path: data.total
fields:
  external_id: noticeId
  title: noticeTitle
  publish_date: publishDate
  region: regionName
  buyer: projectOwner
  category: datasetName
  url_template: "https://ygp.gdzwfw.gov.cn/#/44/detail?noticeId={noticeId}"
```

一个 HTML 站点最小配置：

```yaml
id: demo
name: 某省交易中心
type: html
render: true
url: https://example.com/list?page=${page}&keyword=${keyword}
selectors:
  item: ".list li"
  title: "a.title"
  link: "a.title"
  publish_date: ".date"
  region: ".region"
  buyer: ".buyer"
  content: ".summary"
```

新增后在 `config.yaml` 里加 `demo: true`，运行 `bidradar doctor` 验证。

---

## 9. 项目结构

```text
src/bidradar/
  models.py          统一 Tender 模型
  textutil.py        日期/金额/文本解析
  db.py              SQLite 存储、去重、检索、导出、监控规则
  config.py          YAML 配置
  http.py            httpx 客户端、限速、重试
  engine.py          多源编排、增量同步、监控执行
  detail.py          详情抓取：ccgp HTTP、cebpub/SPA 走 Crawl4AI
  notify.py          Webhook（飞书/企微/钉钉/Slack/Discord）
  cli.py             Typer CLI
  mcp_server.py      FastMCP Server
  sources/
    cebpub.py        中国招标投标公共服务平台
    ggzy.py          全国公共资源交易平台
    ccgp.py          中国政府采购网
    ted.py           EU TED
    sam.py           US SAM.gov
    searxng.py       SearXNG 全网发现（可选）
    yaml_site.py     YAML 驱动的省市级站点
sites/
  gd.yaml            广东省公共资源交易平台
  js.yaml            江苏省公共资源交易平台
  zj.yaml            浙江省公共资源交易服务平台
```

---

## 10. 后续可加

- [x] 省级交易中心连接器（广东、浙江、江苏），按 YAML 配置
- [x] cebpub/ccgp 详情解析（ccgp 直连；cebpub 可接 Crawl4AI）
- [ ] 更多省级站点（四川、山东、湖北等）
- [ ] SearXNG 全网发现 + 自动站点识别
- [ ] 附件/招标文件下载与解析（PDF/Word）
- [ ] 中标结果与供应商画像
- [ ] 向量检索/语义匹配（"找和我业务最相关的标"）
- [ ] Web UI（FastAPI + 简单看板）
- [ ] 代理池与分布式抓取（Scrapling/Crawlee）
