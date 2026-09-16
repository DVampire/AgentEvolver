# NVIDIA（NVDA）历史研究数据：API、覆盖年限与粒度对照

核查日期：2026-09-11。用途：为美股单资产因子与因子策略研究选数据源，覆盖此前列出的行情、公司行动、基本面、事件、新闻和扩展数据。关联：[美股数据需求](us-stock-factor-data-requirements.md)、[总体方案](factor-strategy-mining-agent.md)。

本文检索了监管机构、交易所、公司及供应商的官方网页/API 文档，并通过 HTTPS 实际读取 NVDA 的 SEC submissions 和 companyfacts。商业 API 尚未使用付费账户实测，未购买或建设全量数据集。本轮不改研究代码。

## 1. 如何理解表中的“能获取多久”

NVIDIA 的交易代码为 `NVDA`，SEC CIK 为 `0001045810`；1999-01-22 上市。因此，股票成交历史不可能早于该上市日，但上市申报和财务报告可能包含更早信息。[NVIDIA 官方 FAQ](https://investor.nvidia.com/investor-resources/faqs/default.aspx)、[本次读取的 SEC 公司索引](https://data.sec.gov/submissions/CIK0001045810.json)

- **实测**：本次直接读取 NVDA 公开 API 后得到的结果；不等于已经验收全历史完整性。
- **官方目录/文档**：供应商公开宣称的产品或具体接口覆盖；NVDA 的首条有效记录、字段缺失及账户权限仍需抽样确认。
- **未确认**：官方材料没有给出足够的历史或版本信息；不编造起始年份，不作为已经就绪的回测输入。
- 年数按核查日粗略计算；“至今”指供应商最新可用记录，可能为日终、延迟或实时，不代表所有套餐实时可见。
- **数据粒度、时间戳精度、更新频率分开**：逐笔事件可以带纳秒时间戳，但不代表每纳秒有数据或时间准确度达到纳秒；财报 API 每天更新，财务观察仍是季度/年度；新闻有秒级发布时间，仍是事件数据。

下表的路径示例省略认证和分页参数，并非可直接匿名调用的完整请求。供应商只按其官方文档和官方域名列入；未将搜索结果中的第三方镜像、非官方代理或示例密钥作为数据来源。

## 2. 行情、逐笔成交与盘口

| 编号 / 数据 | 官方 API 与 NVDA 请求方式 | 官方历史覆盖及 NVDA 边界 | 数据粒度 | 访问条件与关键口径 |
| --- | --- | --- | --- | --- |
| A1 日线/分钟 OHLCV、VWAP、笔数 | Massive：`GET /v2/aggs/ticker/NVDA/range/1/minute/{from}/{to}`；日线将 `minute` 换为 `day` | **2003-09-10 起，约 23 年**；接口级文档，NVDA 未实测。[覆盖与接口](https://www.massive.com/docs/rest/stocks/aggregates/custom-bars) | 1 分钟、日及更大聚合周期 | Basic 2 年、Starter 5 年、Developer 10 年、Advanced 全历史；按方案权限。`adjusted=false` 取原始口径；该调整参数处理拆股，不能当成含分红总收益 |
| A2 日线/分钟 OHLCV | Alpaca：`GET /v2/stocks/bars?symbols=NVDA&timeframe=1Min&start=…&end=…`；日线 `1Day` | **2016 年起，约 10 年**；产品文档，NVDA 首条需核验。[覆盖](https://docs.alpaca.markets/us/docs/about-market-data-api)、[bars API](https://docs.alpaca.markets/us/reference/stockbars) | 1–59 分钟、小时、日、周、月 | 需要账户/API key；显式指定 `feed` 并核验权限。IEX 是单市场，SIP 是跨场所汇总，不能拼接成相同成交量口径 |
| A3 长历史日线 | Alpha Vantage：`GET /query?function=TIME_SERIES_DAILY&symbol=NVDA&outputsize=full` | 官方称 **25+ 年**；没有给出 NVDA 专属首日，不能直接承诺从 IPO 起完整。[日线接口](https://www.alphavantage.co/documentation/#daily) | 日；另有周/月接口 | API key；完整历史/调整版本按套餐。原始日线与 `TIME_SERIES_DAILY_ADJUSTED` 区分 |
| A4 长历史分钟线 | Alpha Vantage：`GET /query?function=TIME_SERIES_INTRADAY&symbol=NVDA&interval=1min&month=2000-01&outputsize=full` | 文档支持 **2000-01 起逐月查询，约 26 年**；NVDA 各月连续性未实测。[分钟接口](https://www.alphavantage.co/documentation/#intraday) | **1/5/15/30/60 分钟** | Premium；按月取完整历史，单次 `full` 不代表全部年份。原始/调整、是否含延长时段需显式配置 |
| A5 日线及调整后收益参考 | Sharadar：`GET /v1.0/data/stocks?ticker=NVDA&from=…&to=…`，表别名 SEP | 数据库 **1997-12 起**；NVDA 成交历史最早只能是 1999-01-22，实际首日待核验。[字段与覆盖](https://sharadar.com/docs/stocks) | 日 | 付费数据；默认 OHLCV 为拆股调整口径，另有原始 close 和含分红/分拆调整 close；完整原始 OHLCV 的重建规则需按文档核对 |
| A6 逐笔成交 | Massive：`GET /v3/trades/NVDA?timestamp.gte=…&timestamp.lt=…` | **2003-09-10 起，约 23 年**；NVDA 未实测。[成交 API](https://www.massive.com/docs/rest/stocks/trades-quotes/trades) | 逐笔事件；接口含纳秒时间戳 | Developer 10 年、Advanced 全历史；价格、股数、场所、条件码、更正和序列号。逐笔可派生秒线，但要定义合格成交集合 |
| A7 历史最优买卖报价 / NBBO | Massive：`GET /v3/quotes/NVDA?timestamp.gte=…&timestamp.lt=…` | **2003-09-10 起，约 23 年**；NVDA 未实测。[报价 API](https://www.massive.com/docs/rest/stocks/trades-quotes/quotes) | 逐条报价事件；纳秒时间戳 | 页面列 Advanced 全历史；不能因为 Developer 有逐笔成交就推断它也包含报价。NBBO 不是完整深度订单簿 |
| A8 逐笔成交与报价的另一来源 | Alpaca：`GET /v2/stocks/trades?symbols=NVDA&start=…`、`GET /v2/stocks/quotes?symbols=NVDA&start=…` | **2016 年起**的产品覆盖；NVDA 各 feed/字段需实测。[覆盖](https://docs.alpaca.markets/us/docs/about-market-data-api)、[成交](https://docs.alpaca.markets/us/reference/stocktrades-1)、[报价](https://docs.alpaca.markets/us/reference/stockquotes-1) | 逐笔/逐条报价；可自行聚合成秒级 | 需要认证和相应 feed 权限；完整返回须遍历 `next_page_token`，页大小不是历史长度 |
| A9 秒线、逐笔与深度订单簿 | Databento：`/v0/timeseries.get_range`；`dataset=XNAS.ITCH, symbols=NVDA`；选择 `schema=ohlcv-1s / trades / mbo / mbp-1` 等 | **NVDA 专属官方目录明确列 2018-05-01 起，约 8 年**；每个 schema 的首尾仍需按账户查询。[NVDA 目录](https://databento.com/catalog/us-equities/XNAS.ITCH/equities/NVDA)、[HTTP 请求](https://databento.com/docs/getting-started/build-first-app?historical=http&live=http) | 原生 **1 秒 OHLCV**、逐笔成交、逐订单事件；可构建更大周期 | 商业数据与场所授权按账户核验；`XNAS.ITCH` 是 Nasdaq 场所数据，不能冒充全美市场的完整成交或全场所订单簿 |

对于“秒级数据”，首选验证 A9 的原生 `ohlcv-1s`；另一条明确路径是 A6/A8 逐笔数据自行聚合。本表没有把某供应商的实时 1 秒 WebSocket 推送当成它也提供全部历史秒线的证据。

## 3. 财报、股本、市值与公司行动

| 编号 / 数据 | 官方 API 与 NVDA 请求方式 | 能获取多久 / 证据 | 数据粒度 | 使用边界 |
| --- | --- | --- | --- | --- |
| B1 原始申报、财报及重要公告索引 | SEC：`GET /submissions/CIK0001045810.json`，再读取其 `filings.files` 指向的历史分页；通过 accession 和 primaryDocument 取申报原文 | **实测：索引含 1998-03-06 至 2026-09-08 的范围信息**；旧分页另取。不同表单各自起点不同。[NVDA 索引](https://data.sec.gov/submissions/CIK0001045810.json)、[API 说明](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | 每次申报事件；10-Q/10-K 为季度/年度，8-K 等为事件 | 公开无需 key；不能只取 recent。索引起点不代表从同一天起已具备全部表单、字段和新闻 |
| B2 结构化三表项目及部分股数 | SEC：`GET /api/xbrl/companyfacts/CIK0001045810.json`；也可使用 companyconcept 单项目接口 | **实测：本次检查的营收/净利润/经营现金流包含截至 2008-01-27 的期间，但这些抽样项目最早 filed 为 2009-08-20**。资产抽样的最早期间为 2009-01-25。[NVDA companyfacts](https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json) | 季度、年度、累计期间或时点余额；随申报增加版本 | 2008 年期间值不能当成 2008 年就从该数据源可知。字段和单位覆盖不一致，自定义/分部项目可能需解析原文；股数项目另核验起点 |
| B3 标准化财务、盈利质量、股本等 | Sharadar：`GET /v1.0/data/fundamentals?ticker=NVDA&dimension=ARQ`；另取 `ARY/ART` | 数据库 **1998-01 起**；NVDA 各项目实际起点未实测。[财务字段与维度](https://sharadar.com/docs/fundamentals) | 季度、年度、TTM；服务每日更新 | 付费。回测优先评估 AR；MR 含后续重述。AR 是 filing 日期维度，不自动覆盖更早的业绩新闻稿时刻 |
| B4 标准化利润表的另一来源 | Massive：`GET /stocks/financials/v1/income-statements?tickers=NVDA` | 接口标注 **2009-03-29 起，约 17 年**；NVDA 未实测。[利润表 API](https://massive.com/docs/rest/stocks/fundamentals/income-statements) | 季度、年度、TTM | 页面列 Advanced 或 Financials 扩展权限；其 `filing_date` 是最近包含该期间数值的文件日期，不是原始公开日，不能直接当 PIT 面板 |
| B5 日级市值与估值 | Sharadar：`GET /v1.0/data/daily?ticker=NVDA&from=…&to=…` | 数据库 **1998-12 起**；NVDA 不早于上市且受财务覆盖约束。[DAILY 文档](https://sharadar.com/docs/daily) | 日 | 官方称 As-Reported；市值、EV、PE 等的单位/分母要核对。日更估值不表示财务分母每日出现新信息 |
| B6 历史证券参考与部分股本字段 | Massive：`GET /v3/reference/tickers/NVDA?date=YYYY-MM-DD` | 接口 **2003-09-10 起**；不保证每个早期日期都有全部股数字段。[历史参考 API](https://www.massive.com/docs/rest/stocks/tickers/ticker-overview) | 按日期查询参考状态 | Basic 2 年、付费档页面列全历史；用稳定证券标识和 CIK 辅助映射，不能仅按 ticker 拼接 |
| B7 自由流通股快照 | Massive：`GET /stocks/vX/float?ticker=NVDA` | **仅 latest；没有可承诺的历史回溯**。[Float 定义](https://massive.com/docs/rest/stocks/fundamentals/float) | 最新有效快照 | 不能拿今天自由流通股数回填历史。首次接入以后可以按发布与抓取时间存档；长历史需另找可验证的 PIT 产品 |
| B8 拆股 / 合股 | Massive：`GET /stocks/v1/splits?ticker=NVDA` | 接口全库最早 **1978-10-25**，不是 NVDA 起点；NVDA 以自身实际事件为准。[新拆股接口](https://massive.com/docs/rest/stocks/corporate-actions/splits) | 公司行动事件，日期级；服务日更 | Basic 2 年、Starter 及以上页面列全历史；保存原始比例、生效日和调整因子 |
| B9 现金分红 | Massive：`GET /stocks/v1/dividends?ticker=NVDA` | 接口全库最早 **2000-01-15**；NVDA 从自身实际分红事件起，未实测首条。[新分红接口](https://massive.com/docs/rest/stocks/corporate-actions/dividends) | 事件；公告/除息/登记/支付日期 | 依套餐历史权限；保留金额币种，区分原始和拆股调整后每股金额；不把无分红年份填成缺失价格 |
| B10 更完整的公司行动/证券沿革候选 | Sharadar：`GET /v1.0/data/actions?ticker=NVDA&from=…&to=…` | 数据库 **1998-01 起**；NVDA 对应事件未实测。[ACTIONS 文档](https://sharadar.com/docs/actions) | 事件日期级；服务日更 | 付费，含更名、拆股、分红、分拆、上市退市及关联证券等；具体复杂事件的对价和披露时间仍需核验 |

**公司行动的官方页面存在范围表述差异。** Massive 股票首页概述公司行动可追溯到 2008 年，而上述新拆股/分红接口分别标注 1978/2000 年。本表采用具体接口文档并保留这个差异，不能据此宣布 NVDA 所有早期事件已完整覆盖。[股票首页](https://www.massive.com/stocks)

## 4. 新闻、业绩事件与分析师预期

| 编号 / 数据 | 官方 API 与 NVDA 请求方式 | 官方历史覆盖 | 数据粒度 / 更新 | 必须区分的问题 |
| --- | --- | --- | --- | --- |
| C1 新闻标题与可得正文 | Alpaca / Benzinga：`GET /v1beta1/news?symbols=NVDA&start=…&end=…&include_content=true` | **2015 年起，约 11 年**；新闻产品覆盖，NVDA 首篇及完整性未实测。[历史说明](https://docs.alpaca.markets/us/docs/historical-news-data)、[接口](https://docs.alpaca.markets/us/reference/news-3) | 单篇事件；发布时间/更新时间，持续新增 | 需要认证及适用权限；正文仅在 available 时返回，不能承诺每条都有正文；Alpaca 的上游是 Benzinga |
| C2 聚合新闻元数据 | Massive：`GET /v2/reference/news?ticker=NVDA&published_utc.gte=…` | **2016-06-22 起，约 10 年**。[新闻 API](https://www.massive.com/docs/rest/stocks/news) | 单篇事件 | 主要为标题、描述、来源和文章链接；不能当成具有完整历史正文授权的新闻库，附加情绪字段也需核验生成时间 |
| C3 历史业绩、实际值/预期值/惊喜 | Benzinga 直连：`GET /api/v2.1/calendar/earnings`，过滤 `parameters[tickers]=NVDA` 和日期 | 直连产品页称 **2012 年起，约 14 年**。[覆盖](https://www.benzinga.com/apis/cloud-product/corporate-earnings/)、[API](https://docs.benzinga.com/api-reference/calendar-api/get-earnings) | 季度业绩事件；含日期/时间，日内更新 | 付费授权；实际 EPS、预期 EPS 和 GAAP/调整口径要匹配；事件记录不等于每日预期修订档案 |
| C4 同类业绩数据的另一接入方式 | Massive / Benzinga：`GET /benzinga/v1/earnings?ticker=NVDA&date.gte=…` | 该转售接口标注 **2010-04-30 起，约 16 年**。[接口及历史](https://www.massive.com/docs/rest/partners/benzinga/earnings) | 业绩事件，实时更新服务 | 需要 Benzinga Earnings 扩展；与 C3 上游相同，不算独立证据源。其起点与直连产品描述不同，要核验具体合同与 NVDA 首条 |
| C5 可用于预期修订研究的历史共识 | LSEG I/B/E/S；通过 LSEG Data Library `get_data` 请求 `TR.EPSMean`、`TR.RevenueMean`、对应 `calcdate`，设置历史区间与 `Frq=D`；NVDA 的 RIC 需证券映射确认 | **未核实 NVDA 专属首日及账户可回溯年限**；官方示例支持按历史计算日期取预期。[历史预期示例](https://developers.lseg.com/en/article-catalog/article/fundamentals-estimates-dcf)、[当前数据目录](https://www.lseg.com/en/data-catalogue/company-data) | 日级历史快照；目标期间为季度/年度等 | 机构商业授权；本轮未做有权限查询。必须保留预测目标期与观察日，不能用“美国数据库有几十年”替代 NVDA 的实际范围 |
| C6 普通分析师估计接口候选 | FMP：`GET /stable/analyst-estimates?symbol=NVDA&period=quarter&page=0&limit=…` | **NVDA 历史长度、完整 as-of 版本能力未确认**。[官方接口](https://site.financialmodelingprep.com/developer/docs/stable/financial-estimates) | 按预测目标季度/年度返回 | 有旧报告期间并不能证明能还原某天看到的预期；未确认前不用于“历史预期上修/下修”回测 |

B1 的 8-K/财报原文同时是原始公告来源。公司业绩新闻稿可能早于 10-Q/10-K 提交，新闻、业绩事件和财务申报时间应分别记录；它们不是可以互换的同一时间字段。

## 5. 其他输入与回测配套

| 编号 / 数据 | 官方 API 与 NVDA 请求方式 | 官方历史覆盖 | 数据粒度 | 限制与用途 |
| --- | --- | --- | --- | --- |
| D1 空头持仓 Short Interest | Massive：`GET /stocks/v1/short-interest?ticker=NVDA` | **2017-12-29 起，约 8 年**；Basic 2 年，其他股票档页面列全历史。[接口](https://massive.com/docs/rest/stocks/fundamentals/short-interest) | 按申报批次，约每月两次 | 统计日不是公开日；需要发布日期对齐。该指标是持仓量 |
| D2 空头成交量 Short Volume | Massive：`GET /stocks/v1/short-volume?ticker=NVDA` | **2024-02-06 起，约 2 年半**。[接口](https://massive.com/docs/rest/stocks/fundamentals/short-volume) | 日 | FINRA 场外报告口径；不是空头持仓，也不是全美全部场所的方向资金流 |
| D3 监管原始空头持仓候选 | FINRA Query API：`/data/group/otcMarket/name/consolidatedShortInterest`，按 `symbolCode=NVDA` 过滤 | 2023 年官方上线说明为 **5 年回溯**，并注明 **2022-06 起含所有交易所**；当前可取首日与历史分表须查元数据，不把旧公告直接推算为今天的完整覆盖。[API 目录](https://developer.finra.org/docs)、[上线说明](https://developer.finra.org/release-notes/january-2023-release-notes) | 约每月两次；按公开批次 | 认证与数据权限按 FINRA 现行规则；不能查询 Mock 然后把示例数据当 NVDA 历史 |
| D4 历史期权链、IV 和 Greeks | Alpha Vantage：`GET /query?function=HISTORICAL_OPTIONS&symbol=NVDA&date=YYYY-MM-DD` | 文档接受 **2008-01-01 之后的历史交易日，约 18 年**；NVDA 各合约未实测。[期权接口](https://www.alphavantage.co/documentation/#historical-options) | 按日的期权链快照 | Premium；这不是秒级期权行情。Greeks/IV 的模型和快照口径还需验证 |
| D5 期权分钟线和逐笔 | Alpaca：`GET /v1beta1/options/bars`、`GET /v1beta1/options/trades`；`symbols` 使用 NVDA 的历史 OCC 合约代码 | **2024-02 起，约 2 年半**。[历史范围](https://docs.alpaca.markets/us/docs/historical-option-data)、[bars](https://docs.alpaca.markets/us/reference/optionbars)、[trades](https://docs.alpaca.markets/us/reference/optiontrades) | 分钟及更大 bars、逐笔事件 | OPRA 需订阅；免费 Indicative 是衍生指示数据，不能当原始 OPRA。股票 2016 年历史不能套用到期权 |
| D6 内部人持股与交易 | Sharadar：`GET /v1.0/data/insiders?ticker=NVDA`（SF2） | 数据库 **2008-01 起，约 18 年**。[字段与历史](https://sharadar.com/docs/insiders) | Form 3/4/5 披露事件；服务日更 | 保留交易日与 filing 日，不能在披露前用到内部人交易 |
| D7 机构持仓 | Sharadar：`GET /v1.0/data/holdings?ticker=NVDA`（SF3） | 数据库 **2013-06 起，约 13 年**。[字段与历史](https://sharadar.com/docs/holdings) | 季度持仓；服务日更 | 表中日期为持仓季度末；文档字段没有足够信息直接证明完整 PIT，需关联机构申报时间/历史版本。SEC 原文在各持有人申报中，不是查 NVDA 自己的 companyfacts 即可得到 |
| D8 市场/行业参照 | 重用 A1/A2/A5 的 bars 接口，将证券换为适用基准或 ETF，例如 `SPY`、`SMH` | 各证券自身上市历史与供应商覆盖/套餐的交集；不能给所有参照套用 NVDA 首日 | 日、分钟；逐笔按行情产品 | ETF 行情可以从股票行情接口取；指数和历史成分可能另有授权。研究历史行业归属时，当前分类快照不够 |
| D9 利率与宏观 | FRED：`GET /fred/series/observations?series_id=DGS10&observation_start=…&file_type=json`；历史版本使用相应 realtime/vintage 参数 | 例如 **DGS10 为 1962-01-02 起**；其他序列各有起点。[观测 API](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)、[DGS10 历史](https://fred.stlouisfed.org/series/DGS10/data) | DGS10 为日；其他指标可能周/月/季度 | 需要 FRED key；观察日期不等于市场可见时间。不能把今天修订后的宏观值当成历史发布值 |
| D10 交易日历与提前收市 | Alpaca Paper：`GET /v2/calendar?start=…&end=…` | 官方参考列 **1970–2029**。[日历 API](https://docs.alpaca.markets/us/v1.1/reference/getcalendar-1) | 交易日级；附开闭市时间 | 需认证；这是通用市场日历，不含每只股票全部停牌历史，未来日期仍可能调整 |
| D11 交易状态与停牌事件候选 | Databento：`/v0/timeseries.get_range`，`dataset=XNAS.ITCH, symbols=NVDA, schema=status` | NVDA 产品目录从 **2018-05-01** 起且列有 Status；**status 自身最早日期须查询 metadata，不能直接承诺等于产品起点**。[NVDA 目录](https://databento.com/catalog/us-equities/XNAS.ITCH/equities/NVDA)、[状态字段](https://databento.com/docs/knowledge-base) | 事件级，包含时间戳 | 按 schema/场所核验状态语义与覆盖；不能仅凭没有成交推断停牌 |
| D12 借券供给与借券费率候选 | IBKR 历史发布记录曾提供 `GET /hmds/history`，`barType=Inventory/FeeRate` | **当前 NVDA 可回溯年限、粒度、现版入口及权限未确认**。[官方功能发布记录](https://www.ibkrguides.com/releasenotes/api/cp-web/beta-2022.htm) | 历史 bars，具体可用周期待账户验证 | 这是待确认候选，不是已经验证可调用的现行接口；没有历史可借性与费用时，不声称已完成可执行做空回测 |
| D13 手续费与成交成本 | 用 A7/A8 历史报价计算点差，用成交估计冲击；费用取选定券商和场所的适用费表 | **没有核实到能统一返回 NVDA 全历史实际费用的公开 API**；费率要按生效时间另建版本 | 报价/成交为事件级，费表为生效区间 | 实际佣金依账户、订单和场所；报价不是保证成交价。不能拿今天“零佣金”替代全部历史交易成本 |

## 6. API 服务地址与认证

| 来源 | 官方服务地址 | 请求与认证说明 |
| --- | --- | --- |
| SEC | `https://data.sec.gov`；申报原文从 `https://www.sec.gov/Archives/edgar/data/…` 获取 | 公共只读 HTTPS；按官方要求声明 User-Agent 并控制速率。原文路径从索引取得 |
| Massive | `https://api.massive.com` | 本文列的股票、新闻及合作方查询均为只读 GET；API key 和具体扩展权限按合同。旧 Polygon 品牌不等于另一独立数据源 |
| Alpaca 行情/新闻 | `https://data.alpaca.markets` | 行情头认证 `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY`；不得将凭据发给第三方转发服务 |
| Alpaca 日历示例 | `https://paper-api.alpaca.markets` | 只读 `/v2/calendar`；不需要为了取日历发起真实交易 |
| Alpha Vantage | `https://www.alphavantage.co/query` | `function` 区分数据集；API key 与 Premium 权限；不要把 `demo` 当成 NVDA 授权 |
| Sharadar | `https://api.sharadar.com/v1.0/data/…` | 本表按当前官方原生 REST 文档列路径；`api_key` 认证。Nasdaq Data Link 的 SF1/SEP 等别名属于另一交付入口，账户与接口权限不要混用 |
| Databento | `https://hist.databento.com/v0/…` | Historical API/官方 SDK；HTTP 支持查询请求，按官方规范认证。用 `metadata.get_dataset_range` 检查账户与 schema 范围 |
| Benzinga | `https://api.benzinga.com` | 官方 calendar API 使用 token；与 Massive 合作方扩展是不同账户/合同入口 |
| FMP | `https://financialmodelingprep.com/stable/…` | 官方稳定版接口；本文不使用相似域名的第三方 API 镜像 |
| FINRA | `https://api.finra.org` | Query API 通过 GET 或只读 POST 查询；production、HISTORIC 与 Mock 数据集分清 |
| FRED | `https://api.stlouisfed.org` | `/fred/series/observations`，按官方 API key 认证 |
| LSEG | 官方 Data Library / 数据平台账户 | 通过官方 SDK 建立授权会话，本文没有猜测专属 REST 路径或假设 NVDA 的历史权限 |

“可信安全”的边界是：**来源为可追溯的一手机构或供应商，使用其官方 HTTPS/API 与合法数据权限**；这不代表商业宣传、历史内容或服务永远无误。本次没有登录交易账户、提交交易权限、使用来历不明的 key 或安装第三方采集程序。认证信息不要写入 Markdown、Git 或带密钥的访问日志；新闻正文和再分发范围须看实际许可。

## 7. 本次 SEC 实测证据

本次仅在内存读取两个公开 JSON，约 4.25 MB，没有落盘为行情/财务训练集。记录的是抽样事实，不推断未经检查的项目起点。

| 请求 / 项目 | 本次结果 | 能说明什么 |
| --- | --- | --- |
| `submissions/CIK0001045810.json` | 160,053 bytes；名称 NVIDIA CORP；ticker NVDA；recent 范围 2020-07-06 至 2026-09-08 | 公司身份及近期索引可读 |
| 上述索引的 `filings.files` | 指向 `CIK0001045810-submissions-001.json`；目录标注 1,476 条，1998-03-06 至 2020-07-04 | 更早记录要读取该历史分页，不能只用 recent；本轮未逐条验收旧分页 |
| `companyfacts/CIK0001045810.json` | 4,092,966 bytes；名称 NVIDIA CORP | 结构化公开财务 API 可读 |
| `Assets` | 138 条；最早 end=2009-01-25，最早 filed=2009-08-20，最晚 filed=2026-08-26 | 期间与公开时间不同 |
| `NetIncomeLoss` | 314 条；最早 end=2008-01-27，最早 filed=2009-08-20，最晚 filed=2026-08-26 | 更早比较期间不是更早可见版本 |
| `Revenues` | 280 条；最早 end=2008-01-27，最早 filed=2009-08-20，最晚 filed=2026-08-26 | 营收标签需与其他营收 taxonomy 按期间核对 |
| `NetCashProvidedByUsedInOperatingActivities` | 161 条；最早 end=2008-01-27，最早 filed=2009-08-20，最晚 filed=2026-08-26 | 仍需区分季度、累计和年度现金流 |

以上计数包含不同期间/重复申报或比较值，不能当成互相独立的季度数。来源：[公司索引](https://data.sec.gov/submissions/CIK0001045810.json)、[companyfacts](https://data.sec.gov/api/xbrl/companyfacts/CIK0001045810.json)。

## 8. 对 NVDA 第一批数据的建议

| 研究需求 | 优先核验的数据组合 | 历史窗口安排 |
| --- | --- | --- |
| 日频量价与基本面 | A1 或 A2 行情 + B1/B2 SEC + B8/B9 或 B10 公司行动；若采用结构化库，再核验 B3/B5 | 可以先以 **2016 年至最新完整交易日**为共同目标窗口；最终以各字段验收为准 |
| 希望覆盖更早市场阶段 | A3/A4/A5 或 A1 的完整历史 + 相应公司行动 + 可恢复公开时间的财务 | 价格历史可以更长；不能为对齐而伪造 2009 年之前的 XBRL 可见性 |
| 业绩与新闻因子 | 在基础包上加 C1 与 C3/C4，原始业绩发布用 SEC/公司披露核对 | 新闻与业绩有各自覆盖区间，按相同重叠区间比较增量，不删除全部更早量价样本 |
| 订单流与秒级研究 | A6+A7 或 A8；需要 Nasdaq 深度/状态再用 A9/D11 | 秒级报价/订单簿成本与规模单独评估；不能用单场所结果代替全市场结果 |

正式获取前，对每个候选保存：`security_id, dataset, schema/feed, first_valid_record, last_valid_record, entitlement_window, timestamp_semantics, adjustment_basis, missing_intervals, source_version`。Databento 可先查 `metadata.get_dataset_range` 的逐 schema 范围；其返回的是账户可访问范围，仍不是单只 NVDA 每个时点均有记录的保证。[官方范围查询](https://databento.com/docs/api-reference-historical/basics/authentication?historical=http&live=http)

目前仍需明确的缺口是：NVDA 全历史自由流通股的 PIT 版本、分析师预期的实际授权回溯范围、13F 的披露版本对齐、历史借券及费用。它们不阻碍先准备量价、财报和公司行动，但相应因子不能在缺少数据时被当成已经支持。
