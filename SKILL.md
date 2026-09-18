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
# 0. 准备配置（仓库自带 MMO 示例，复制即用；随后把 project 节改成你的产品画像）
cp config/mmo-example.json config/competitor-watch.json     # Windows: Copy-Item

# 1. 解析各竞品的渠道入口（hykb 全自动；taptap/official 生成占位）
python scripts/resolve.py

# 2. 采集（hykb 源无需任何密钥即可抓到评分+公告）
python scripts/ingest.py

# 3. 出报告
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

**统一配置 `config/competitor-watch.json` 的 6 个 section**：

| Section | 管什么 | 关键字段 |
|---------|--------|---------|
| `project` | 我的项目（评分参照系） | enabled 开关、name/genre/stage 画像、core_systems 等七组关键词字段、knowledge.summary 项目概要（知识库） |
| `games` | 竞品列表（用户主要维护的） | name（唯一）、aliases、genre、priority、type=`game`\|`industry` |
| `sources` | 数据源接入 | wcrss.feed_url（微信 RSS）、fetch_defaults |
| `scoring` | 评分体系 | dimensions（含每维度 1–5 分标准 criteria + project_refs 项目字段引用 + boost/penalty 通用关键词）、weights、tiers |
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

三类数据源均由统一配置 `sources` 节的 **enabled 开关**控制（`config-wizard.html`「数据源」页签可视化操作）。**关闭的渠道 ingest.py 采集时、resolve.py 解析时都会自动跳过**；重新开启后重跑一次 `resolve.py` 即可恢复。`ingest.py --source <id>` 显式指定时无视开关强制运行（便于测试）。

| 渠道 | 开关 | 自动化程度 | 数据类型 | 接入方式 |
|------|------|-----------|---------|---------|
| `wechat` 微信公众号 | `sources.wcrss.enabled` | 半自动 | 文章正文 | 需 RSS 聚合服务，地址填 `sources.wcrss.feed_url` 或环境变量 `WCRSS_FEED_URL`（未配置时 ingest 友好跳过） |
| `official` 游戏官网 | `sources.official.enabled` | **自动配置** | 公告 / RSS 快照 | **无需手动接入**：games 更新后 resolve.py 自动为每个新竞品生成条目（见下方流程） |
| `hykb` 好游快爆 | `sources.extra_sources[].enabled` | 全自动 | 评分 + 更新公告 | resolve.py 自动搜索 fid，零配置 |
| `taptap` | `sources.extra_sources[].enabled` | 半自动 | 评分快照 | resolve 生成占位 + `_hint` 搜索链接，按提示补 app_id 到 `derived/taptap.json` |
| 自定义源 | `sources.extra_sources[].enabled` | 自定义 | 自定义 | 页签添加条目 + `scripts/sources/<id>.py` 提供 `run(**kw) -> (new, dup)`（参考 `hykb.py`，标准库），ingest 自动发现 |

**新增竞品标准流程**（官网源在此流程中自动配置）：

```
1. 修改 config/competitor-watch.json 的 games 数组（加一行）
2. python scripts/resolve.py
   ├─ wcrss: 已订阅 → 自动写 mp_id ✅；未订阅 → 打印 Subscription Plan → ⚠️ 必须询问用户同意后才引导订阅
   ├─ hykb: 自动搜索定位 fid ✅（置信不足会留 _hint）
   ├─ official: ★ 自动为新竞品生成官网条目（derived/official.json）——用户无需手动填任何 URL；
   │            已有条目的 url/rss/selectors 原样保留；url 为空的占位条目 ingest 时自动跳过不报错
   └─ taptap: app_id 为空 → 给 _hint 链接让用户补
3. python scripts/ingest.py        # 只跑已开启的数据源
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

**评分参照系（project 节，「我的项目」）**：评分不是真空进行的——`project` 节描述用户自己的产品画像：

| 字段 | 被哪个维度引用（project_refs） | 内容 |
|------|------|------|
| `core_systems` | A 战略契合（+2 档） | 核心系统关键词，如 血盟/帮派/直购 |
| `target_users.overlap_keywords` | B 用户匹配（+2 档） | 目标用户重叠关键词 |
| `mount_modules` | C 玩法可迁移（+1 档） | 可挂载模块清单 |
| `calendar.key_nodes` | E 周期合理（+2 档） | 运营节点关键词（周年/节气/节日） |
| `differentiation` | F 差异化（+2 档） | 差异化方向关键词 |
| `monetization` | G 付费拉动（+2 档） | 商业化模型关键词 |
| `risk_redlines` | I 风险等级（-2 档） | 风险红线关键词 |

机制：`project.enabled=true` 时，score.py 把各维度 `project_refs` 引用的字段自动展开并入对应档位的关键词——**改产品画像一处，七个维度的打分口径自动跟随**；关闭后各维度仅按 boost/penalty 通用关键词打分（完全向后兼容）。`knowledge.summary` 是提炼过的项目概要（知识库），**规则引擎不消费它**，但 agent 在人工分析、写深度报告、回答"这个动态对我产品意味着什么"时应先读它理解语境。报告文件头会标注当时的参照系（名称·品类·阶段）并附 project 快照注释，供日后回溯评分基准。

**默认 9 维度**（全部可在配置中增删，脚本自动适配；每维度在配置中含 `desc` 衡量说明与 `criteria` 1–5 分判分标准；D 实施成本与 H 用户声量为通用维度，不引用项目字段）：

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

完整判分标准与关键词见配置文件 `scoring.dimensions`（每个维度的 `criteria` 字段给出 1–5 分逐档描述，`boost`/`penalty` 为通用命中规则，`project_refs` 为项目字段引用）。用户可通过 config-wizard.html「评分体系」页签可视化调整（第一个卡片即「我的项目」）。

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

1. **首次运行**：检查 `config/competitor-watch.json` 是否存在。不存在 → 引导用户复制 `config/mmo-example.json` 或打开 `config-wizard.html` 配置。**配置第 0 步永远是 project 节**：引导用户把「我的项目」卡片改成自己的产品画像（名称/核心系统/目标用户/商业化等），再调竞品列表
2. **增改竞品后**：先 `resolve.py` 再 `ingest.py`。**官网源在此步自动配置**——resolve 会为新竞品自动生成官网条目占位，不要让用户手动填 URL（除非用户想升级 rss/selectors 抓取）
3. **数据源开关**：ingest/resolve 自动跳过 `enabled=false` 的渠道。用户重新打开某源开关后，提醒重跑一次 `resolve.py` 再采集
4. **wcrss 缺订阅**：必须先问用户同意，不得自行添加订阅
5. **报告/数据异常**：先查 `data/articles.db`（sqlite3）有无数据，再查 `config/derived/` 配置是否正确，最后核对数据源开关状态
6. **正式发布前**：先 `--dry-run` 预览，确认内容无误后再推
7. **用户问"怎么用/怎么配"**：指向 `config-wizard.html`（根目录，双击打开，第一个页签是完整用户手册）
8. **用户想接入新数据源**（B站/贴吧等）：在 `sources.extra_sources` 加条目 + 在 `scripts/sources/<id>.py` 写采集器（`run(**kw) -> (new, dup)`，参考 `hykb.py`），无需改任何现有脚本
9. **用户改了 project 节（产品方向调整/新阶段）**：提醒重跑 `python scripts/score.py --rescore-all` 全量重打，再重新生成受影响日期的报告；报告头的参照系标注会自动更新
10. **人工分析/深度解读前**：先读 `project.knowledge.summary` 项目概要理解语境（规则引擎不消费它，但它是你理解"这个动态对用户产品意味着什么"的钥匙）；若概要明显过时，提示用户更新

### 禁止做的事

1. 不要直接修改 `config/derived/`（由 resolve.py 生成；taptap app_id 与 official url 例外，按 _hint 引导用户补）
2. 不要把 `config/competitor-watch.json` 提交到公开仓库（已在 .gitignore；含 feed token / webhook 等敏感信息）
3. 不要把抓取间隔调得太小（脚本内置 ≥1s，避免触发风控）
4. 不要删除或重置 `data/` 目录（用户的历史数据）
5. 不要在未征得用户同意的情况下添加新的数据源订阅

### 错误处理

| 症状 | 排查步骤 |
|------|---------|
| ingest 无数据 | ① 核对数据源开关（sources 节 enabled）② 看 `derived/` 各文件有无有效 id / fid ③ wechat 源确认 feed_url 有效 |
| 报告全空 | 该日无文章属正常；或查 articles.db 确认 |
| 评分全 C/D | ① project 节是否启用且画像与品类匹配 ② scoring.dimensions 通用关键词是否匹配 → 引导用户在面板调整 |
| 改了「我的项目」评分没变 | 存量文章需 `score.py --rescore-all` 全量重打后重新生成报告 |
| publish 报错 | tdoc：token 过期 / root_folder_id 无效；wecom：webhook 失效 |
| wcrss missing | 引导用户去 RSS 服务后台添加订阅 → 重跑 resolve.py |
| 官网源没数据 | 属正常（占位条目自动跳过）；补 url/rss/selectors 后即可采集 |
| 配置读取失败 | 跑 `python scripts/config_loader.py` 自检（打印各 section 与数据源开关概况） |

---

## 九、安全与隐私

- 敏感信息（token、webhook、file_id）集中在 `config/competitor-watch.json`，已被 `.gitignore` 排除；`config/mmo-example.json` 是唯一随仓库分发的示例（不含真实凭证）
- API Key 优先读环境变量：`WCRSS_FEED_URL`、`TDOC_ACCESS_TOKEN`、`WECOM_WEBHOOK_URL`
- 旧版拆分配置已归档在 `.backup-*/`（不随仓库分发）

---

## 十、路线图

- [x] Phase 1: 脱敏 + 可视化配置面板
- [x] Phase 2: 配置统一（单一 JSON + 内嵌用户手册 + 评分标准）
- [x] Phase 3: 数据源扩展框架（extra_sources 可扩展机制、三源开关、官网随游戏列表自动配置；B站/小红书等渠道按框架接入即可）
- [x] Phase 3.5: 项目画像参照系（project 节 + project_refs 声明式注入 + 报告参照系标注与快照 + knowledge 知识库）
- [ ] Phase 4: LLM 洞察层（智能分析 section，超越关键词匹配；可消费 project.knowledge 语境）
- [ ] Phase 5: 品类画像系统（多品类 project + 维度关键词模板包一键切换；stage 阶段感知权重）
- [ ] Phase 6: 多项目管理（按项目分文件，多产品线并行监测）
