---
name: competitor-watch
description: 游戏竞品自动化监测。当用户要求"拉取竞品文章/公众号更新/评分/玩家口碑"、"看今日竞品动态"、"生成竞品日报/周报/月报/年报"、"添加竞品/新增游戏"、"竞品监测"等时触发。架构：用户只维护一份 games.json 顶层游戏列表，agent 自动派生 wcrss/hykb/taptap/official 的 derived 配置。多渠道：微信公众号（wcrss）+ TapTap 评论评分 + 好游快爆评分公告 + 官网/官方论坛。SQLite 去重持久化，Markdown 日/周/月/年报。支持多品类（MMO/SLG/卡牌/RPG 等），通过 games.json 的 genre 字段区分。
agent_created: true
---

# Competitor Watch — 游戏竞品监测 Skill

> **面向 Agent 的操作手册**。本文件定义了 agent 在执行竞品监测任务时的全部流程、命令和规则。

---

## 一、快速参考

### 用户一句话，Agent 做什么？

| 用户说 | Agent 执行 |
|--------|-----------|
| "拉一下今天的竞品动态" | `ingest.py` → `report.py daily` → 展示结果 |
| "出个周报" | `report.py weekly`（自动取上周数据） |
| "添加原神为竞品" | 改 `games.json` → `resolve.py` → 确认 derived |
| "竞品评分怎么样了" | 查 `data/reports/` 或跑 `score.py` 单独看 |
| "推到腾讯文档" | `publish.py daily` / `weekly` / `monthly` |
| "帮我配一下监测" | 引导使用 `config/config-wizard.html` |

### 核心命令速查

```bash
# ═══ 初始化 / 配置变更后 ═══
python scripts/resolve.py              # 重建 derived 配置

# ═══ 数据采集 ═══
python scripts/ingest.py               # 全部源
python scripts/ingest.py --source hykb # 单源

# ═══ 报告生成 ═══
python scripts/report.py daily                 # 昨天的日报（工作日默认）
python scripts/report.py daily --date 2026-08-11
python scripts/report.py daily --catch-up 7    # 补齐过去 7 天
python scripts/report.py weekly                # 上周
python scripts/report.py monthly               # 上月
python scripts/report.py yearly                # 去年

# ═══ 发布 ═══
python scripts/publish.py daily               # 推送到腾讯文档 / 本地 / 企微
python scripts/publish.py daily --dry-run      # 预览不实际推
python scripts/publish.py daily --no-wecom     # 跳过企微
```

---

## 二、架构总览

```
config/
├── games.json              ★ 用户维护（竞品列表 + 品类标签）
├── config.json             敏感配置（RSS URL、抓取参数）
├── scoring.json            评分维度 + 权重 + 关键词规则
├── publish.json            发布目标（腾讯文档 / 企微 / 本地）
├── report_format.json       报告格式（章节、分类关键词、长度限制）
├── config-wizard.html      ★ 可视化配置面板（导入/导出/编辑）
└── derived/                ★ agent 自动生成（勿手动改）
    ├── wcrss.json          微信公众号 mp_id 映射
    ├── hykb.json           好游快爆 fid 解析
    ├── taptap.json         TapTap app_id 占位
    └── official.json       官网/论坛 URL 占位

scripts/
├── resolve.py              games.json → derived/ 派生
├── ingest.py               多源采集调度
├── score.py                9 维度评分引擎（关键词命中）
├── report.py               Markdown 报告生成
├── publish.py              发布（腾讯文档 / 企微 / 本地文件）
├── db.py                   SQLite 存储 + 去重
├── wcrss_client.py         RSS 客户端
└── sources/
    ├── _common.py          共享工具
    ├── wechat.py           微信公众号文章
    ├── taptap.py           TapTap 评分 + 评论
    ├── hykb.py             好游快爆评分 + 公告
    └── official.py         官网/论坛

data/
├── articles.db             SQLite（文章 + 评分快照）
├── raw/                    原始 HTML 缓存
└── reports/{daily,weekly,monthly,yearly}/*.md   # 生成的报告
```

### 数据流

```
用户编辑 games.json
       │
       ▼
  resolve.py ──→ derived/*.json（各数据源配置）
       │
       ▼
  ingest.py ──→ articles.db（去重入库）
       │
       ▼
  score.py ──→ 每篇文章打分（9 维度 × 权重）
       │
       ▼
  report.py ──→ data/reports/*/*.md（Markdown 报告）
       │
       ▼
  publish.py ──→ 腾讯文档 / data/reports/（本地） / 企微群
```

---

## 三、配置管理

### 3.1 用户如何配置

**推荐方式**：双击打开 `config/config-wizard.html`

这是一个单文件可视化面板，功能：
- **导入**：从已有的 JSON 文件或备份一键恢复配置
- **游戏列表**：增删竞品、设品类标签、优先级
- **密钥 & 服务**：RSS Feed URL、抓取参数
- **评分维度**：动态增减维度、调权重、设评级阈值
- **发布设置**：本地输出（默认 md/docx）、腾讯文档、企微机器人
- **导出**：下载 4 个 JSON 配置文件到 config/ 目录
- **实时同步**：所有修改自动保存到 localStorage

**手动方式**：直接编辑 `config/` 下的 JSON 文件。

### 3.2 games.json Schema

```json
{
  "_comment": "顶层游戏列表。增删游戏 = 在 games 数组加一行。",
  "_schema": {
    "name": "中文显示名（必填唯一）",
    "type": "game | industry",
    "aliases": "搜索别名（数组）",
    "priority": "high | normal | low",
    "genre": "品类标签",
    "notes": "备注"
  },
  "games": [
    { "name": "原神",     "aliases": ["Genshin"],          "genre": "open_world_rpg", "priority": "high",  "type": "game" },
    { "name": "王者荣耀", "aliases": ["HoK","Honor of Kings"],"genre": "moba",          "priority": "high",  "type": "game" },
    { "name": "GameLook", "aliases": [],                     "genre": "",             "priority": "normal","type": "industry" }
  ]
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | ✅ | 全局唯一标识，用于 db 匹配和报告显示 |
| `aliases` | ❌ | 用于 wcrss 反向匹配公众号名称 |
| `type` | ❌ | `game`=游戏竞品，`industry`=行业媒体号 |
| `priority` | ❌ | 影响日报排序和抓取顺序 |
| `genre` | ❌ | 品类标签，影响评分关键词匹配（Phase 2 后启用） |

### 3.3 scoring.json — 评分体系

9 维度评分引擎，每篇文章按关键词命中得分：

| 维度 ID | 名称 | 默认权重 | 含义 |
|---------|------|---------|------|
| A_strategy_fit | 战略契合度 | 1.2 | 是否匹配自家产品核心方向 |
| B_user_match | 用户匹配度 | 1.0 | 目标用户画像重叠度 |
| C_portability | 玩法可迁移性 | 1.1 | 能否借鉴到自家产品 |
| D_cost_inverse | 实施成本(反向) | 0.9 | 复制难度越高分越低 |
| E_cycle_fit | 周期合理性 | 0.8 | 是否匹配当前版本节奏 |
| F_differentiation | 差异化潜力 | 1.0 | 与市场已有方案的差异度 |
| G_pay_drive | 付费拉动力 | 1.1 | 对营收的潜在拉动 |
| H_voice | 用户声量 | 0.9 | 玩家讨论热度 |
| I_risk_inverse | 风险等级(反向) | 1.0 | 风险越高分越低 |

**评级阈值**（可自定义）：
- **S** ≥36: 强烈推荐 · 战略级
- **A** ≥32: 可立项 · 进策划评审
- **B** ≥28: 差异化借鉴 · 排进版本
- **C** ≥22: 存档参考
- **D** <22: 忽略

---

## 四、数据源

| 渠道 | 自动化程度 | 数据类型 | 配置方式 |
|------|-----------|---------|---------|
| `wechat` 微信公众号 | 半自动（需订阅 RSS 服务） | 文章正文 | `derived/wcrss.json` |
| `taptap` | 半自动（需补 app_id） | 评分 + 评论 | `derived/taptap.json` |
| `hykb` 好游快爆 | **全自动** | 评分 + 更新公告 | `derived/hykb.json` |
| `official` 官网/论坛 | placeholder（需手动配置） | 链接快照 / RSS | `derived/official.json` |

### 新增竞品后的标准流程

```
1. 编辑 games.json（添加新竞品）
        │
2. 运行 python scripts/resolve.py
        │
   ├─ wcrss: 检查是否已订阅对应公众号
   │   ├─ 已订阅 → 自动写入 mp_id 到 derived/wcrss.json ✅
   │   └─ 未订阅 → 打印 Subscription Plan → ⚠️ agent 必须询问用户同意后才引导订阅
   │
   ├─ hykb: 自动搜索定位 fid ✅
   │
   ├─ taptap: 若 app_id 为空 → 给出 _hint 链接让用户补
   │
   └─ official: 生成 placeholder
        │
3. 运行 python scripts/ingest.py   # 采集数据
4. 运行 python scripts/report.py daily  # 出报告
```

**关键约束**：wcrss 订阅变化必须经用户明确同意。Resolve 只打印 plan，不主动下单。

---

## 五、报告生成（Agent 核心指令）

### 5.1 日报

**何时出**：每个工作日早上，覆盖**前一天**的全部数据。

```bash
# 标准用法（默认取昨天）
python scripts/report.py daily

# 指定日期
python scripts/report.py daily --date 2026-08-11

# 补齐过去 N 天缺失的报告（已存在的跳过，幂等安全）
python scripts/report.py daily --catch-up 7

# 强制重新生成已存在的报告
python scripts/report.py daily --catch-up 7 --force
```

**语义规则**：
- 日报内容**严格按 `publish_time` 过滤**——"8月11日的日报"只含 8月11日发布的文章
- 工作日 12:00 跑默认拿到的是**昨天的完整一天**
- 当 `--date` 是当天时，报告头会插入 **"⚠️ 内容不完整"** 警告（当日尚未结束）
- `--catch-up` 扫到脏标记文件会**自动重生**

**日报结构**（由 `config/report_format.json` 控制）：

```markdown
# 竞品日报 2026-08-11（周一）

## 概览
- 监测竞品：N 个
- 数据源：微信公众号 / TapTap / 好游快爆 / 官方论坛
- 本期文章：M 条 | 高信号(≥B)：K 条

## 摘要
### 📌 高信号事件（Score ≥ B）
| 竞品 | 来源 | 标题 | 评分 | 维度命中 |

### 🏷️ 分类汇总
| 分类 | 数量 | 涉及竞品 |
|------|------|----------|
| 新版本 | 3 | 原神、燕云、逆水寒 |
| 活动运营 | 5 | ... |

### 📊 评分分布
| S | A | B | C | D |
|---|---|---|---|---|

## 📆 日报原文清单（按 publish_time 严格过滤）
[按竞品分组，列出每篇文章的标题/来源/时间/摘要]
```

### 5.2 周报

**何时出**：每周一早上，覆盖**上一周一~周日**的数据。

```bash
python scripts/report.py weekly
```

**周报在日报基础上增加**：
- TOP 5 重点事件（跨天聚合）
- 评分趋势观察（哪些竞品评分上升/下降）
- 玩家口碑关键词（来自低分评论）
- 每个竞品 1 句话总结
- 本期建议关注（2-3 个值得深挖的方向）

### 5.3 月报 / 年报

```bash
python scripts/report.py monthly   # 上月
python scripts/report.py yearly    # 去年
```

结构与周报类似，但时间跨度更大，增加：
- 月度/年度评分走势对比
- 长期趋势判断（持续升温 vs 偶发事件）

### 5.4 报告格式定制

所有报告格式集中在 `config/report_format.json`，可随时编辑即时生效：

| 可配项 | 说明 |
|--------|------|
| `content_tags` | 文章分类关键词（新增分类 = 加一组关键词） |
| `source_labels` | 数据源显示名和排列顺序 |
| `limits` | 各类长度截断（标题数/描述字数） |
| `section_order` | 章节渲染顺序 |
| `kind_titles` | 日报/周报/月报/年报的大标题前缀 |
| `dirty_marker` | 当日不完整的警告文案 |

---

## 六、发布

### 6.1 发布渠道

| 渠道 | 配置位置 | 开关 |
|------|---------|------|
| **本地文件**（默认） | `publish.json` → `local_output` | `enabled: true`（默认） |
| 腾讯文档 | `publish.json` → `tencent_docs` | `enabled: false`（需配置） |
| 企业微信 | `publish.json` → `wecom_bot` | `enabled: false`（需配置 webhook） |

### 6.2 发布命令

```bash
# 日报（每天一份，label=YYYY-MM-DD）
python scripts/publish.py daily
python scripts/publish.py daily --date 2026-08-11
python scripts/publish.py daily --catch-up 7

# 周报/月报/年报（每期一份）
python scripts/publish.py weekly
python scripts/publish.py monthly
python scripts/publish.py yearly

# 预览模式（看会做什么但不实际执行）
python scripts/publish.py daily --dry-run

# 跳过企微推送
python scripts/publish.py daily --no-wecom
```

### 6.3 腾讯文档行为

- Token 从 WorkBuddy 连接器自动读取（环境变量 `TDOC_ACCESS_TOKEN`）
- 每个 `(kind, label)` 一份独立文档：如 `daily/2026-08-11` 一份、`weekly/2026-W32` 一份
- 同 label 重跑 = **覆盖刷新**（不复用旧文档内容）
- **按月归档**：设置 `root_folder_id` 后，自动创建 `YYYY-M/` 子文件夹并归档
- `doc_ids` 和 `doc_urls` 由 agent 自动回填到 `publish.json`

---

## 七、Agent 行为规范

### 7.1 必须做的事

1. **首次运行前**：检查 `config/games.json` 是否存在且非空。若不存在，引导用户使用 `config-wizard.html` 或从示例模板复制
2. **增改竞品后**：必须跑 `resolve.py` 再跑 `ingest.py`
3. **wcrss 缺订阅**：必须先问用户同意，不得自行添加订阅
4. **报告出错**：先查 `articles.db` 是否有数据，再查 `derived/` 配置是否正确
5. **发布前确认**：`--dry-run` 预览一遍，确认内容无误后再正式推

### 7.2 禁止做的事

1. 不要直接修改 `derived/` 中的文件（它们由 `resolve.py` 生成）
2. 不要把 `config.json` / `publish.json` / `games.json` 提交到 git（已在 `.gitignore`）
3. 不要把抓取间隔调得太小（默认 1s，避免触发风控）
4. 不要在未征得用户同意的情况下添加新的数据源订阅

### 7.3 错误处理指南

| 症状 | 排查步骤 |
|------|---------|
| ingest 无数据 | 检查 `derived/wcrss.json` 有无 mp_id；检查 RSS URL 是否有效 |
| 报告全空 | 检查 `--date` 对应日期是否有文章；查看 `db.articles` 表 |
| 评分全是 D | 检查 `scoring.json` 的关键词是否匹配当前品类 |
| publish 报错 | 检查 token 是否过期；检查 `root_folder_id` 是否有效 |
| wcrss missing | 引导用户去 RSS 服务后台添加订阅 → 重跑 `resolve.py` |

---

## 八、安全与隐私

- 所有敏感配置（token、API key、file_id）存储在 `config/*.json`，已被 `.gitignore` 排除
- 备份文件 `config/local_backup/secrets.backup.json` 同样被排除
- 公开仓库中只有 `*.example.json` 模板（不含真实凭证）
- API Key 优先读环境变量：`WCRSS_FEED_URL`、`TDOC_ACCESS_TOKEN`、`WECOM_WEBHOOK_URL`

---

## 九、路线图

- [x] Phase 1: 脱敏 + 配置面板 + .gitignore
- [ ] Phase 2: 品类画像系统（`genre_profiles/`，多品类评分适配）
- [ ] Phase 3: 数据源扩展（B站视频 / 小红书 / 贴吧）
- [ ] Phase 4: LLM 洞察层（智能分析 section，超越关键词匹配）
- [ ] Phase 5: 多项目管理（按项目分文件，多产品线并行监测）
