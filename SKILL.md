---
name: competitor-watch
description: 游戏竞品自动化监测。当用户要求"拉取竞品文章/公众号更新/评分/玩家口碑"、"看今日竞品动态"、"生成竞品日报/周报/月报/年报"、"添加竞品/新增游戏"、"竞品监测"等时触发。多渠道采集（微信公众号 RSS + TapTap + 好游快爆 + 官网论坛）、9 维度规则评分、Markdown 报告、可发布腾讯文档/企微。单一 JSON 配置文件，纯 Python 标准库，支持任意品类（MMO/SLG/卡牌等）。
agent_created: true
---

# Competitor Watch — 游戏竞品监测 Skill

> **本文件是 Agent 的操作手册**：定义执行竞品监测任务时的全部流程、命令和规则。
> 面向人类的图文手册在 `config-wizard.html`（浏览器打开，第一个页签即用户手册）——两者内容保持一致。

---

## 一、概述

**做什么**：用户在一份 JSON 里维护竞品游戏列表，本 skill 自动从 4 个渠道采集动态、按可配置的多维度规则打分、生成日/周/月/年报，并可推送到腾讯文档 / 企业微信。

**技术约束**：
- Python 3.10+，**全部脚本只用标准库**（urllib / sqlite3 / json），无需 pip install
- 所有命令在 skill 根目录执行
- 可在任何 AI 编码助手（Claude / ChatGPT / Gemini / WorkBuddy 等）中运行，无平台绑定

**数据流**：

```
config/competitor-watch.json（唯一配置文件）
        │  resolve.py
        ▼
config/derived/*.json（各渠道入口，agent 自动维护）
        │  ingest.py
        ▼
data/articles.db（SQLite，去重入库 + 自动打分）
        │  report.py
        ▼
data/reports/{daily,weekly,monthly,yearly}/*.md
        │  publish.py
        ▼
腾讯文档 / 企微群 / 本地
```

---

## 二、快速开始（首次使用）

```bash
# 1. 准备配置（仓库自带 MMO 示例，复制即用）
cp config/mmo-example.json config/competitor-watch.json     # Windows: Copy-Item

# 2. 解析各竞品的渠道入口（hykb 全自动；taptap/official 生成占位）
python scripts/resolve.py

# 3. 采集（hykb 源无需任何密钥即可抓到评分+公告）
python scripts/ingest.py

# 4. 出报告
python scripts/report.py daily
```

报告输出到 `data/reports/daily/<日期>.md`。微信公众号源需先配置 RSS 聚合服务（见第四节）。

---

## 三、目录结构与配置

```
config-wizard.html          ★ 可视化配置面板 + 用户手册（浏览器直接打开）
SKILL.md                    本文件（agent 手册）
config/
├── competitor-watch.json   ★ 唯一配置文件（5 个 section，见下表）
├── mmo-example.json        MMO 示例配置（供新用户试跑，勿直接编辑使用）
└── derived/                agent 自动生成（勿手动改）
    ├── wcrss.json          公众号 mp_id 映射
    ├── hykb.json           好游快爆 fid
    ├── taptap.json         TapTap app_id（需按 _hint 补齐）
    └── official.json       官网/论坛 URL
scripts/                    resolve / ingest / score / report / publish / db / config_loader
data/                       articles.db + raw/ 原始缓存 + reports/ 生成的报告（勿删）
.backup-*/                  旧版配置归档（仅迁移期存在）
```

**统一配置 `config/competitor-watch.json` 的 5 个 section**：

| Section | 管什么 | 关键字段 |
|---------|--------|---------|
| `games` | 竞品列表（用户主要维护的） | name（唯一）、aliases、genre、priority、type=`game`\|`industry` |
| `sources` | 数据源接入 | wcrss.feed_url（微信 RSS）、fetch_defaults |
| `scoring` | 评分体系 | dimensions（含每维度 1–5 分标准 criteria + boost/penalty 关键词）、weights、tiers、mount_points |
| `publish` | 发布渠道 | tencent_docs、wecom_bot、local_backup |
| `report` | 报告格式 | content_tags、limits、section_order、kind_titles、dirty_marker |

**games 数组字段**：

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | ✅ | 全局唯一，用于 db 匹配和报告显示 |
| `aliases` | ❌ | 搜索别名，用于 wcrss 反向匹配公众号 |
| `type` | ❌ | `game`=游戏竞品；`industry`=行业媒体号（评分降权，不抓渠道评分） |
| `priority` | ❌ | `high`/`normal`/`low`，影响日报排序 |
| `genre` | ❌ | 品类标签（mmo/slg/card/open_world_rpg/…） |
| `notes` | ❌ | 备注 |

**修改配置的三种方式**：① 用户在 `config-wizard.html` 面板编辑后导出覆盖；② 直接编辑 JSON；③ 让 agent 改。**改了 games 后必须重跑 `resolve.py`**。

---

## 四、数据源

| 渠道 | 自动化程度 | 数据类型 | 接入方式 |
|------|-----------|---------|---------|
| `hykb` 好游快爆 | **全自动** | 评分 + 更新公告 | resolve.py 自动搜索 fid，零配置 |
| `wechat` 微信公众号 | 半自动 | 文章正文 | 需 RSS 聚合服务，地址填 `sources.wcrss.feed_url` 或环境变量 `WCRSS_FEED_URL` |
| `taptap` | 半自动 | 评分 + 评论 | resolve 生成占位 + `_hint` 搜索链接，按提示补 app_id 到 `derived/taptap.json` |
| `official` 官网/论坛 | 手动配置 | 链接快照 / RSS | 在 `derived/official.json` 填 url（可选 rss / CSS selectors） |

**新增竞品标准流程**：

```
1. 修改 config/competitor-watch.json 的 games 数组（加一行）
2. python scripts/resolve.py
   ├─ wcrss: 已订阅 → 自动写 mp_id ✅；未订阅 → 打印 Subscription Plan → ⚠️ 必须询问用户同意后才引导订阅
   ├─ hykb: 自动搜索定位 fid ✅（置信不足会留 _hint）
   ├─ taptap: app_id 为空 → 给 _hint 链接让用户补
   └─ official: 生成 placeholder
3. python scripts/ingest.py
4. python scripts/report.py daily
```

**关键约束**：wcrss 订阅变化必须经用户明确同意。resolve 只打印 plan，不主动下单。

---

## 五、评分体系

每篇文章按 **关键词规则引擎**（无 LLM）打分：

1. 每维度起始 **3 分**（中性），命中 boost 关键词上调（如 +2/+1）、命中 penalty 关键词下调，叠加来源倾向（source_weight_bias）
2. 钳制在 1–5 分，乘以维度权重求和 → 总分
3. 按 tiers 档位评级（默认 S≥36 / A≥32 / B≥28 / C≥22 / D）
4. `industry` 类型竞品整体降分（默认 -3），不参与 TOP 推荐

**默认 9 维度**（全部可在配置中增删，脚本自动适配；每维度在配置中含 `desc` 衡量说明与 `criteria` 1–5 分判分标准）：

| 维度 | 名称 | 默认权重 | 一句话标准（5 分 ↔ 1 分） |
|------|------|---------|--------------------------|
| A_strategy_fit | 战略契合度 | 1.2 | 直接命中核心系统焦点 ↔ 与产品方向无关 |
| B_user_match | 用户匹配度 | 1.0 | 用户画像高度重叠 ↔ 完全不同人群 |
| C_portability | 玩法可迁移性 | 1.1 | 现有模块直接挂载 ↔ 依赖全新引擎重构 |
| D_cost_inverse | 实施成本(反向) | 0.9 | 零成本配置化 ↔ 大版本级投入 |
| E_cycle_fit | 周期合理性 | 0.8 | 完美契合当前节点 ↔ 无周期参考价值 |
| F_differentiation | 差异化潜力 | 1.0 | 市场稀缺题材 ↔ 纯粹模仿 |
| G_pay_drive | 付费拉动力 | 1.1 | 直接拉动 ARPU ↔ 无付费拉动 |
| H_voice | 用户声量 | 0.9 | 破圈级传播 ↔ 无人关注 |
| I_risk_inverse | 风险等级(反向) | 1.0 | 官方授权零风险 ↔ 私服/违规高风险 |

完整判分标准与关键词见配置文件 `scoring.dimensions`（每个维度的 `criteria` 字段给出 1–5 分逐档描述，`boost`/`penalty` 为引擎实际命中规则）。用户可通过 config-wizard.html「评分体系」页签可视化调整。

```bash
python scripts/score.py                # 增量：只打未打分的（ingest 后会自动跑）
python scripts/score.py --rescore-all  # 全量重打（改配置后用）
python scripts/score.py --since 2026-06-01 --dry-run  # 预览
```

---

## 六、报告生成

```bash
python scripts/report.py daily                    # 昨天的日报（默认）
python scripts/report.py daily --date 2026-08-11  # 指定日期
python scripts/report.py daily --catch-up 7       # 补齐过去 7 天（幂等，缺哪天补哪天）
python scripts/report.py daily --catch-up 7 --force
python scripts/report.py weekly                   # 上周一~周日
python scripts/report.py monthly                  # 上月
python scripts/report.py yearly                   # 去年
```

**语义规则**：
- 日报内容**严格按 publish_time 过滤**——"8月11日的日报"只含当日 00:00–24:00 (CST) 发布的文章
- `--date` 为当天时，报告头自动插入"⚠️ 内容不完整"警告 + 文件尾 dirty marker；catch-up 扫到 marker 自动重生
- 报告结构（概览 / 摘要 / 原文清单）、分类关键词、标题文案全部由配置 `report` 节控制，改配置即时生效

---

## 七、发布

```bash
python scripts/publish.py daily                # 推昨天的日报
python scripts/publish.py daily --catch-up 7   # 批量补推
python scripts/publish.py weekly / monthly / yearly
python scripts/publish.py daily --dry-run      # 预览不实际推（正式推前建议先跑）
python scripts/publish.py daily --no-wecom     # 跳过企微
```

**渠道行为**：

| 渠道 | 开关 | 行为 |
|------|------|------|
| 本地 Markdown | 恒开 | report.py 直接写 `data/reports/` |
| 腾讯文档 | `publish.tencent_docs.enabled` | 每 `(kind, label)` 一份独立文档；同 label 重跑=覆盖刷新；设置 root_folder_id 后按月归档（自动建 YYYY-M 子文件夹）；doc_ids/doc_urls 由脚本自动回填配置 |
| 企微机器人 | `publish.wecom_bot.enabled` | 推摘要卡片；高信号事件 @all |

**Token 发现顺序**：环境变量 `TDOC_ACCESS_TOKEN` / `WCRSS_TDOC_TOKEN` → 配置 `publish.tencent_docs.access_token` → `~/.mcporter/credentials.json`。

---

## 八、Agent 行为规范

### 必须做的事

1. **首次运行**：检查 `config/competitor-watch.json` 是否存在。不存在 → 引导用户复制 `config/mmo-example.json` 或打开 `config-wizard.html` 配置
2. **增改竞品后**：先 `resolve.py` 再 `ingest.py`
3. **wcrss 缺订阅**：必须先问用户同意，不得自行添加订阅
4. **报告/数据异常**：先查 `data/articles.db`（sqlite3）有无数据，再查 `config/derived/` 配置是否正确
5. **正式发布前**：先 `--dry-run` 预览，确认内容无误后再推
6. **用户问"怎么用/怎么配"**：指向 `config-wizard.html`（根目录，双击打开，第一个页签是完整用户手册）

### 禁止做的事

1. 不要直接修改 `config/derived/`（由 resolve.py 生成；taptap app_id 与 official url 例外，按 _hint 引导用户补）
2. 不要把 `config/competitor-watch.json` 提交到公开仓库（已在 .gitignore；含 feed token / webhook 等敏感信息）
3. 不要把抓取间隔调得太小（脚本内置 ≥1s，避免触发风控）
4. 不要删除或重置 `data/` 目录（用户的历史数据）
5. 不要在未征得用户同意的情况下添加新的数据源订阅

### 错误处理

| 症状 | 排查步骤 |
|------|---------|
| ingest 无数据 | 看 `derived/` 各文件有无有效 id / fid；wechat 源确认 feed_url 有效 |
| 报告全空 | 该日无文章属正常；或查 articles.db 确认 |
| 评分全 C/D | scoring.dimensions 关键词与品类不匹配 → 引导用户在面板调整 |
| publish 报错 | tdoc：token 过期 / root_folder_id 无效；wecom：webhook 失效 |
| wcrss missing | 引导用户去 RSS 服务后台添加订阅 → 重跑 resolve.py |
| 配置读取失败 | 跑 `python scripts/config_loader.py` 自检（打印各 section 概况） |

---

## 九、安全与隐私

- 敏感信息（token、webhook、file_id）集中在 `config/competitor-watch.json`，已被 `.gitignore` 排除；`config/mmo-example.json` 是唯一随仓库分发的示例（不含真实凭证）
- API Key 优先读环境变量：`WCRSS_FEED_URL`、`TDOC_ACCESS_TOKEN`、`WECOM_WEBHOOK_URL`
- 旧版拆分配置已归档在 `.backup-*/`（不随仓库分发）

---

## 十、路线图

- [x] Phase 1: 脱敏 + 可视化配置面板
- [x] Phase 2: 配置统一（单一 JSON + 内嵌用户手册 + 评分标准）
- [ ] Phase 3: 数据源扩展（B站视频 / 小红书 / 贴吧）
- [ ] Phase 4: LLM 洞察层（智能分析 section，超越关键词匹配）
- [ ] Phase 5: 品类画像系统（多品类评分关键词自动适配）
- [ ] Phase 6: 多项目管理（按项目分文件，多产品线并行监测）
