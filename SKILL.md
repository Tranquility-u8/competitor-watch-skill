---
name: competitor-watch
description: MMO 竞品监测 skill。当用户要求"拉取竞品文章/公众号更新/评分/玩家口碑"、"看今日竞品动态"、"生成竞品日报/周报/月报/年报"、"添加竞品/新增游戏"、"竞品监测"等时触发。架构：用户只维护一份 games.json 顶层游戏列表，agent 自动派生 wcrss/hykb/taptap/official 的 derived 配置。多渠道：微信公众号（wcrss）+ TapTap 评论评分 + 好游快爆评分公告 + 官网/官方论坛。SQLite 去重持久化，Markdown 日/周/月/年报。
agent_created: true
---

# Competitor Watch — MMO 竞品监测

## 设计原则（v3）

**用户只维护 `config/games.json`**（顶层游戏列表）；其他配置由 agent 通过 `scripts/resolve.py` 自动派生到 `config/derived/`：

```
games.json (用户维护)
   └─→ resolve.py 解析
         ├→ derived/wcrss.json     反向匹配 wcrss 已订阅公众号 → 缺订阅时报 plan
         ├→ derived/hykb.json      m.3839.com 搜索自动定位 fid
         ├→ derived/taptap.json    生成骨架（app_id 须用户填，给点击式提示链接）
         └→ derived/official.json  生成 placeholder（mode=snapshot），用户后续可升级 rss/selectors
```

**wcrss 订阅变化必须经用户同意。** Resolve 只打印 plan，不主动下单。

## 渠道矩阵

| 渠道 | 自动化程度 | 拿到什么 |
|---|---|---|
| `wechat` 微信公众号 | 反向匹配（wcrss 平台手动订阅，agent 同步映射） | 文章正文 |
| `taptap` | 半自动（提示用户补 app_id） | 评分 + 最近 N 条评论 |
| `hykb` 好游快爆 | **全自动**（m 站搜索 + 严格名称匹配） | 评分 + 最新更新公告 |
| `official` 官网/论坛 | placeholder（用户后续按需升级） | 链接快照 / RSS / HTML 选择器 |

## 工作流

### 第一次跑 / 增改 games.json 后

```bash
# 1. 编辑顶层游戏列表
notepad config/games.json

# 2. 让 agent 自动重建 derived（不会自动订阅 wcrss）
python scripts/resolve.py

# 3. 如果 resolve 提示 "Subscription Plan: 需要订阅 X"，agent 应询问用户是否同意，
#    用户同意后再去 wcrss.com/publishers 添加订阅，下次 resolve 自动同步 mp_id

# 4. 如果 derived/taptap.json 里 app_id 为空，按 _hint 链接补上
```

### 日常拉取 + 出报告

```bash
python scripts/ingest.py                          # 全部源
python scripts/ingest.py --source hykb            # 单源

# Daily：默认 anchor = 昨天（"工作日 12:00 跑昨天"）
python scripts/report.py daily                    # 出昨天的日报
python scripts/report.py daily --date 2026-05-29  # 出指定日期的日报
python scripts/report.py daily --catch-up 7       # 补齐过去 7 天缺失的日报（已存在的跳过）
python scripts/report.py daily --catch-up 7 --force   # 强制重新生成
python scripts/report.py weekly                   # 上周
python scripts/report.py monthly                  # 上月
python scripts/report.py yearly                   # 去年
```

**重要语义（v3.4+）**：
- 日报内容**严格按 `publish_time`** 过滤——`X月Y日的日报`只含**X月Y日发布**的文章
- daily 默认 anchor=**昨天**，即工作日 12:00 跑命令拿到的是"昨天的完整一天"
- `--catch-up N` 扫过去 N 天，**缺哪天补哪天**（已存在文件默认跳过，幂等）
- **脏标记（v3.7）**：当 `--date` 是当日时，报告头会加显著的"内容不完整"警告横幅，文件末加 `<!-- competitor-watch-status: dirty -->`。`catch-up` 扫到脏文件会**自动重生**（不论 anchor 是否还落在 today），刷新后若 label 已不是 today，标记会自动消失。
- 输出格式（关键词分类、长度、章节顺序、警告文案）集中在 `config/report_format.json`，可手动编辑后即时生效。

### 发布到腾讯文档（+ 可选企微）

```bash
# Daily：每天一份新文档（label=YYYY-MM-DD）
python scripts/publish.py daily                       # 推昨天的日报
python scripts/publish.py daily --date 2026-05-29     # 指定日期
python scripts/publish.py daily --catch-up 7          # 扫过去 7 天，缺哪天补哪天

# Weekly/Monthly/Yearly：每期一份新文档
python scripts/publish.py weekly
python scripts/publish.py monthly
python scripts/publish.py yearly

python scripts/publish.py daily --dry-run             # 看会做什么但不实际推
python scripts/publish.py daily --no-wecom            # 跳过企微推送
```

- 配置在 `config/publish.json`，三块开关：tencent_docs / wecom_bot / local_backup
- 腾讯文档 token 自动从 `~/.mcporter/credentials.json` 读取（mcporter OAuth 后产生，1 年有效）
- **每个 (kind, label) 一份新文档**：例如 daily 6/1 一份、6/2 一份；weekly 2026-W22 一份；同 label 重跑会复用并覆盖刷新
- `doc_ids[kind][label]` / `doc_urls[kind][label]` 由 agent 自动回填，首次创建后链接稳定
- **按月归档**：`tencent_docs.root_folder_id` 指向腾讯文档某文件夹后，所有新文档创建后会自动 move 到 `<root>/YYYY-M/` 子文件夹（缺失自动新建）。`month_folders` 缓存子文件夹 id。已存在文档（updated 模式）不会重复 move，假定上次已归档。

## 文件
├── SKILL.md
├── config/
│   ├── games.json              ★ 用户唯一需要维护的文件
│   ├── config.json             wcrss API Key 等敏感配置
│   └── derived/                agent 自动维护，可手动微调
│       ├── wcrss.json          mp_id 反向映射 + missing 列表
│       ├── hykb.json           fid 自动解析结果
│       ├── taptap.json         app_id 占位（半自动）
│       └── official.json       url 占位（用户后续按需填 rss/selectors）
├── scripts/
│   ├── resolve.py              ★ 顶层 → derived 派生工具
│   ├── ingest.py               多源调度
│   ├── report.py               生成 Markdown 报告
│   ├── db.py                   SQLite + 自动迁移
│   ├── wcrss_client.py         wcrss API
│   └── sources/
│       ├── _common.py          shared HTTP / 配置加载
│       ├── wechat.py           按 derived/wcrss.json 过滤范围
│       ├── taptap.py           按 derived/taptap.json + games 优先级排序
│       ├── hykb.py             按 derived/hykb.json
│       └── official.py         按 derived/official.json
└── data/
    ├── articles.db             articles + publishers + rating_snapshots
    ├── raw/                    原始 HTML
    └── reports/{daily,weekly,monthly,yearly}/*.md
```

## games.json schema

```json
{
  "games": [
    {
      "name":     "原神",
      "aliases":  ["Genshin"],
      "is_self":  false,
      "priority": "high",
      "notes":    ""
    }
  ]
}
```

字段：
- `name` 必填，作为竞品的全局唯一标识（在 db 里就是 `articles.competitor`）
- `aliases` 可选，用于 wcrss 反向匹配的别名
- `priority`: `high|normal|low`，影响日报分组排序与抓取顺序（高优先先抓）
- `is_self`: 标记自家产品（保留字段，未来用于"自家产品 vs 竞品"区分）

## 添加 / 删除一个游戏的标准流程

1. **改 games.json**：增 / 删 / 调整优先级
2. **跑 resolve**：`python scripts/resolve.py`
3. **检查 plan**：
   - 若有 wcrss missing → **agent 必须先问用户同意才能引导订阅**
   - 若 hykb 自动解析失败 → 看 derived/hykb.json 里的 `_hint` 链接手动补 fid
   - 若 taptap app_id 缺失 → 同上，按 `_hint` 链接补上
4. **跑 ingest** + **出报告**

## 安全约束

- `config/config.json` 与 `config/derived/*.json` **不进 git**
- API Key 优先读 `WCRSS_API_KEY` 环境变量
- TapTap / hykb 抓取已加 1s 间隔，不要把 limit 调太大触发风控

## 已知 trade-off

- **TapTap 不能自动 resolve**：站点搜索是 SPA + 后端 API 反爬强，可靠的方案就是让用户复制 URL 里的 app_id（一次性配置永久有效）
- **hykb 自动 resolve 有歧义时跳过**：避免误抓"诛仙手游 → 火影忍者手游"这种串味，宁可让用户手动补
- **official 默认是 placeholder**：站点结构千差万别，自动嗅探 RSS 不靠谱；但只要用户有需求随时可升级到 RSS / selectors 模式

## LLM 摘要 Prompt

每篇文章 / 评论：
```
请基于下面的内容，提炼 3-5 个对 MMO 策划有用的要点（版本更新、活动、玩法、营收、玩家口碑、官方动态）。
若与竞品业务无关，输出"（无关）"即可。

来源：{source}（{competitor}）
标题：{title}
时间：{publish_time}
正文：{description_or_full_text}
```

周月年报聚合：
```
你是 MMO 竞品分析师。下面是 {start}~{end} 期间，{N} 个竞品的 {M} 条信息（含微信文章/TapTap 评论/官方公告/评分快照）。
请输出：
1. TOP 5 重点事件（含竞品名、事件类型、对我们 Y5 项目可能的影响）
2. 评分趋势观察（哪些竞品评分变好/变坏 + 原因猜测）
3. 玩家口碑关键词（从 TapTap 1-2 星评论里提）
4. 每个竞品 1 句话总结
5. 本期建议关注（2-3 个值得策划组深挖的方向）

输出 Markdown，无前言后语。
```

## 二期/三期路线

- 自动调度：`automation_update` 每天 09:00 ingest，09:30 daily 报告
- 蝉大师 / 七麦 / Sensor Tower（公司付费账号到位后接）
- TapTap 历史评论增量抓取 + 评论关键词云（jieba + wordcloud）
- official 模式 B/A：当 placeholder 已有用户填的 url，agent 自动嗅探 RSS / 提议 selectors
- 报告升级：PPTX / HTML 看板 / 评分时序图
- 推送通道：iWiki / TAPD Wiki / 腾讯文档
