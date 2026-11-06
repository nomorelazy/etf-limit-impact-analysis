# ETF 涨跌停与现金替代盈亏分析

读取现有 ETF 套利回测的已保存结果，把**股票触价、PCF 股票股数、信号金额权重和现金替代价格盈亏**联系起来。原回测和行情目录只读，不改变交易、资金交收或策略信号。

当前适配 [etf-arb-backtest](https://github.com/nomorelazy/etf-arb-backtest) 的详细归因输出及深市 PCF XML，首先用于现有 159338 回测。沪市上市 ETF 的其他 PCF 格式尚未接入。分钟输入为按 `YYYY-MM/*.parquet` 存储的 `time`、`thscode`、`close`。

## 可以查看什么

- 每笔申购／赎回有哪些股票在**信号时刻**触及涨跌停；截至信号时曾触价、申请时触价、当天曾触价分别记录。
- 每只股票的 PCF 每单位股数、本笔实际股数、信号时金额及占 ETF 申赎篮子的金额权重。
- 现金代理股票实际代买／代卖和期限估值对应的价格盈亏，代理费计入一次；固定现金替代单独还原。
- 原回测走实物买卖的股票显示实物价格影响，现金替代贡献为零，不因触价就捏造现金替代。
- 逐股贡献与原报告的现金替代价格盈亏逐笔对账；信息不完整时保留未确定，完整但不符时失败。
- HTML 总览、逐笔股票详情、股票汇总、权重分组、散点图和 CSV。

## 安装与使用

Python 3.10 或更高版本：

```console
python -m pip install -e .
python -m unittest discover -s tests -v
```

在已有原回测运行环境中，也可以进入本项目目录直接运行 `python -m etf_limit_impact`。

### 分析已完成的回测

```console
python -m etf_limit_impact analyze --run-dir "原回测输出目录"
```

默认在原运行目录下建立新的 `limit_analysis_<时间>_<随机编号>` 子目录。也可传 `--output` 指定全新目录；不覆盖已有非空目录，不向行情或 PCF 原始数据目录写入文件。

```console
python -m etf_limit_impact latest --reports-root "原项目/reports"
```

`latest` 只选择状态为 `completed` 且已有逐笔归因 CSV 的最新回测。可加 `--start 2025-01-02 --end 2025-01-10`，按交易发起日筛选；统计不按到账日重新归属。

迁移电脑后可用 `--minute-root`、`--pcf-root` 指定数据位置。

### 回测完成后自动生成分析

```console
python -m etf_limit_impact run --backtest-root "原回测项目目录" --config "原完整回测配置.json" --output-root reports --premium-threshold 0.0014 --direction redemption
```

上例表示仅赎回、折价门槛 0.14%，只是一次运行参数。`--direction` 要求原回测支持该参数，现有桌面单方向版本支持；不支持时会在启动回测前给出错误，可以省略此参数以使用原配置。这个入口调用原回测程序，成功后再调用本项目，生成一个链接原回测报告和涨跌停归因报告的总入口。原配置不覆盖。

**原有回测命令本身不会被自动改写**；需要自动联动时使用上面的 `run` 入口，手动跑完后则使用 `latest`。

## 限价数据与识别范围

只有分钟收盘价时，默认依据 PCF `PreTradingDay` 的**精确 15:00 收盘价**、股票代码板块及当日 PCF 名称推算研究限价，使用 Decimal 四舍五入到股票最小价格单位。缺前收盘、未知板块、观测价格超出推算限价等情况标记无法确认。

这不是经过除权、新股上市初期、临时规则调整等因素修正的官方每日限价。也不是盘口封单识别；**收盘触价不能证明无法买入或卖出**。不使用这些识别结果回写原策略的可交易性。

支持提供每日限价 CSV：

```csv
date,symbol,upper_limit,lower_limit,no_limit,source,note
2025-01-03,600000.SH,11.00,9.00,false,synthetic_example,合成示例，不能用于真实行情
```

```console
python -m etf_limit_impact analyze --run-dir "运行目录" --limits-csv "正式每日限价.csv" --official-only
```

`--official-only` 禁用所有自动推算，只用提供的每日限价；文件中没有记录的股票保持未知。无涨跌幅限制的股票可填 `no_limit=true` 并留空上下限。程序保留来源字段，文件是否来自官方需要由数据提供者确认。

原数据目录中的 `etf_limit_close.csv` 是旧版“收盘仍在涨跌停”的研究样本，不能直接当成覆盖所有股票日的正式限价表。本项目会从分钟数据重新寻找日内触价，因此也包含收盘已经打开的情况。

## 盈亏与权重口径

申购：信号基准 − 实际替代成本（含代理费）；赎回：实际替代净应收 − 信号基准。实际成交、未成交期限估值及固定替代金额沿用原回测，未成交估值不收成交费、不伪造成交。

固定现金项的信号基准使用原 IOPV 的申购固定现金口径，两方向都先按同一信号基准归因，再与实际申购／赎回金额比较。`159900` 汇总行不重复分配到股票。

权重＝股票在信号时的市场价值／（信号 IOPV × 本笔 ETF 份数）。这是**PCF 信号金额权重**，不是股数占比，也不是定期披露的基金持仓权重。由于存在预估现金与固定现金项，股票权重合计不一定为 100%。单位倍数只乘一次。

现金替代退补／应收本金不是利润。整篮预估现金 E 与实际现金 A 的影响单独保留，现有资料不能将其精确分配到某只股票，因此不作强行分摊。

“触价股票对应的盈亏”是条件归因／关联，**不能直接解释成涨跌停造成的因果损失**。全天曾触价是事后信息，不会进入信号时刻判断；后来的价格异常也不会反过来改变当时已知的分类。

## 输出

| 文件 | 用途 |
|---|---|
| `report.html`、`trades/*.html` | 总览及逐笔股票详情 |
| `stock_contributions.csv` | 全部股票逐笔贡献，含未触价股票 |
| `limit_exposures.csv` | 信号／截至信号／全天曾触价的股票明细 |
| `trade_summary.csv` | 每笔触价权重、盈亏、整篮 E/A 和对账结果 |
| `daily_limit_events.csv` | 股票日触价分钟、区间、限价来源和缺失原因 |
| `weight_groups.csv`、`stock_summary.csv` | 按金额权重、方向、处理方式或股票汇总 |
| `weight_vs_cash_pnl.svg` | 信号与全天事后两个口径的权重／替代盈亏散点图 |
| `summary.json`、`analysis_status.json`、`progress.json` | 金额核对、数据覆盖、成功／失败及进度 |

原回测需要保存 `config.json`、`trades.csv`、`signals.csv`、`orders.csv`、`fills.csv`、`cash_settlements.csv`、`trade_attribution.csv`；有 `fees.csv` 时会交叉核对代理费；`substitution_pricing.csv` 用于未成交期限估值。较旧的输出先用原项目的 `report-attribution` 功能重建逐笔归因。

## 数据与发布

只发布源代码、文档和合成测试数据。真实行情、PCF、回测结果和本地路径配置不上传，`reports/`、`data/`、Parquet 及缓存已在 `.gitignore` 排除。

测试包含触价与缺失分钟、信号时刻不读取未来、两方向、单位倍数、代理费、部分成交期限估值、固定现金差额、未定价、CLI、回测联动和原文件不变检查。首版 16 项自动测试通过，并对 654 笔既有真实交易完成逐股现金替代盈亏对账。详见 [验证记录](docs/validation.md)、[需求基线](docs/requirements.md) 和 [数据口径](docs/data_contract.md)。
