[English](../README.md) | 简体中文

# Market Lens - 美股分析器

一个基于命令行的美股分析工具，通过可插拔的技术分析算法寻找投资机会。数据来源于 Yahoo Finance，本地缓存为 Parquet 文件。

## 安装

```bash
brew install uv
uv sync
```

## 快速开始

```bash
# 1. 获取股票池（市值 > 50亿美元的美股）
uv run python main.py fetch-universe

# 2. 拉取 OHLCV 行情数据 + 基本面数据
uv run python main.py fetch-data

# 3. 运行分析器（自动更新数据 + 回测排名靠前的结果）
uv run python main.py analyze -s entry_point --top 20
```

## 命令说明

### `fetch-universe`

从 Yahoo Finance 筛选器获取 NYSE + NASDAQ 的美股，缓存至 `data/tickers.parquet`。

```bash
uv run python main.py fetch-universe              # 默认：市值 > 50亿美元
uv run python main.py fetch-universe --cap 10     # 市值 > 100亿美元
uv run python main.py fetch-universe --cap 0      # 所有股票，无市值过滤
```

### `fetch-data`

拉取股票池中所有股票的日线 OHLCV 数据和基本面数据。

```bash
uv run python main.py fetch-data                    # 拉取全部（默认 5 年历史数据）
uv run python main.py fetch-data --years 3          # 3 年历史数据
uv run python main.py fetch-data --full             # 强制全量重新下载
uv run python main.py fetch-data -t AAPL -t MSFT    # 仅拉取指定股票
uv run python main.py fetch-data --ohlcv-only       # 仅拉取行情，跳过基本面
uv run python main.py fetch-data --fundamentals-only
```

**缓存机制**：OHLCV 数据按股票分别缓存为 Parquet 文件，后续运行仅增量拉取新数据。缓存会感知交易日——周末或盘前不会重复拉取。

### `analyze`

运行分析器分析缓存数据。默认会先更新 OHLCV 数据（如缓存已是最新则自动跳过）。

```bash
uv run python main.py analyze -s entry_point                      # 运行分析器（自动更新数据，回测排名靠前的结果）
uv run python main.py analyze -s entry_point --no-update           # 跳过数据更新
uv run python main.py analyze -s entry_point --top 20              # 仅显示前 20 个结果
uv run python main.py analyze -s entry_point --csv                 # 导出结果为 CSV
uv run python main.py analyze -s entry_point -t AAPL -t MSFT       # 分析指定股票
uv run python main.py analyze -s ma_pullback -p pullback_pct=3     # 覆盖分析器参数
```

`analyze` 命令会自动对排名靠前的结果进行回测。最终得分由 60% 分析得分 + 40% 回测得分混合计算。结果中的 `bt` 列格式为 `胜率%/平均收益/样本数`。

### `list-analyzers`

列出所有可用的分析器。

```bash
uv run python main.py list-analyzers
```

### `simulate`

逐日交易模拟，使用分析器的入场/出场逻辑。逐个交易日遍历：分析器发出入场信号时买入，满足出场条件时卖出，全程跟踪组合表现。

```bash
uv run python main.py simulate -s entry_point                          # 全股票池，最近 1 年
uv run python main.py simulate -s entry_point -t AAPL -t MSFT          # 指定股票
uv run python main.py simulate -s entry_point --start 2024-01-01       # 自定义起始日期
uv run python main.py simulate -s entry_point --top 20                 # 按总收益排前 20
uv run python main.py simulate -s entry_point --capital 50000          # 自定义初始资金
uv run python main.py simulate -s entry_point --position-size 0.5      # 每笔交易使用 50% 资金
uv run python main.py simulate -s entry_point -t AAPL --csv            # 导出交易记录为 CSV
uv run python main.py simulate -s entry_point -t AAPL --equity-curve   # 导出权益曲线 CSV
uv run python main.py simulate -s entry_point --no-update              # 跳过数据更新
```

**工作原理：**
- 每只股票同时只持有一个仓位（不重叠交易）
- 默认周期：最近 1 年（可通过 `--start` / `--end` 覆盖）
- 入场：分析器的 `check_entry_signal()` — 使用预计算指标加速
- 出场：分析器的 `check_exit_signal()` — 每个分析器定义自己的出场规则

**Entry Point 出场规则：**
1. 止损：跌破入场价 10%
2. 止盈：涨超入场价 15%
3. MA20 跌破：连续 3 日收盘低于 MA20
4. 急跌：收盘价低于 MA20 超过 5%
5. 放量跌破：成交量超过 20 日均量 2 倍 + 收盘低于 MA20
6. 时间止损：最长持仓 30 天

**输出：** 汇总表（总收益率%、胜率%、平均收益%、最大回撤%、交易次数、平均持仓天数），聚合统计含出场原因分布。单只股票模拟会额外打印详细交易记录。

### `portfolio`

组合级别模拟，使用单一资金池在所有股票间统一调配。每个交易日先检查持仓出场，再买入得分最高的入场信号。回答："如果我过去一年按照分析器信号操作，收益是多少？"

```bash
uv run python main.py portfolio -s entry_point                           # 全股票池，最近 1 年
uv run python main.py portfolio -s entry_point -t AAPL -t MSFT           # 指定股票
uv run python main.py portfolio -s entry_point --max-positions 20        # 更多并发持仓
uv run python main.py portfolio -s entry_point --position-size 0.05      # 每笔仓位 5%
uv run python main.py portfolio -s entry_point --capital 50000           # 自定义初始资金
uv run python main.py portfolio -s entry_point --start 2024-01-01        # 自定义起始日期
uv run python main.py portfolio -s entry_point --top 100                 # 限制为当前信号前 100 名
uv run python main.py portfolio -s entry_point --csv --equity-curve      # 导出交易记录 + 权益曲线
uv run python main.py portfolio -s entry_point --ticker-breakdown        # 显示按股票统计
uv run python main.py portfolio -s entry_point --no-update               # 跳过数据更新
```

**工作原理：**
- 单一资金池（默认 $100,000），最多 10 个并发持仓，每笔仓位为初始资金的 10%
- 每日：先处理出场（释放资金和仓位）→ 再按得分从高到低买入
- 仓位大小基于**初始**资金（不随盈亏变动，保持一致性）
- 每只股票独立预计算指标，确保速度
- 入场优先级：同一天多只股票发出信号时，得分最高的优先买入

**输出：** 汇总面板（总收益率%、年化收益率%、最大回撤%、胜率%、交易次数、平均持仓天数），出场原因分布表。可选：按股票统计（`--ticker-breakdown`）、交易记录（小股票池自动显示）、CSV 导出。

### `backtest`

对指定股票或分析器的头部结果运行均线敏感度回测。遍历历史 OHLCV 数据，寻找趋势排列时的所有均线触及事件，并衡量反弹成功率。

```bash
uv run python main.py backtest -t AAPL -t MSFT               # 回测指定股票
uv run python main.py backtest -s entry_point                  # 先运行分析器，再回测头部结果
uv run python main.py backtest -s entry_point --top 20         # 回测分析结果前 20 名
uv run python main.py backtest -t AAPL --hold-days 10          # 自定义持仓天数（默认 5）
uv run python main.py backtest -t AAPL --strategy max_return   # 使用最大收益策略
uv run python main.py backtest -t AAPL --csv                   # 导出结果为 CSV
```

**策略：**
- `bounce`（默认）-- 从触及日收盘价到 N 个持仓日后收盘价的收益率
- `max_return` -- 持仓窗口内的最大可能收益（最高点）

**输出列：** `win%`（胜率）、`avg%`（平均收益）、`n`（样本数）、按均线分类的明细（`m10w%`、`m10n`、`m20w%`、`m20n`）

**评分：** 胜率与平均收益的加权组合，历史触及次数不足 10 次时会有置信度惩罚。

## 分析器

所有分析器以 **MA5 > MA10 > MA20** 作为核心日线趋势过滤条件。MA50 排列（MA20 > MA50）为可选项，命中时额外加 **+15 分**。

| 分析器 | 核心过滤 | MA50 加分 | 触及/回踩目标 |
|---|---|---|---|
| `entry_point` | MA5 > MA10 > MA20 | MA20 > MA50 时 +15 | MA10/MA20 |
| `strong_pullback` | MA5 > MA10 > MA20 | MA20 > MA50 时 +15 | MA10/MA20 |
| `ma_pullback` | MA5 > MA10 > MA20 | MA20 > MA50 时 +15 | MA5（短期） |

### `entry_point` -- 趋势入场点分析器

寻找处于短期上升趋势中、且在日线 MA10/MA20 支撑位附近出现入场信号的股票。

**过滤条件：**
- 日线 MA5 > MA10 > MA20（短期趋势完好）
- 周线收盘价 > 周线 MA20（中期上升趋势）

**入场信号**（检查最近 3 根 K 线）：
- **HAMMER（锤子线）** -- 长下影线测试 MA10/MA20 后被拒（倒 T 型 / 蜻蜓十字星），最强信号。
- **TOUCH（触及）** -- K 线最低价触及 MA10/MA20，收盘价守住支撑。
- **APPROACHING（接近）** -- 价格向 MA10/MA20 支撑位靠拢。

**加分项：**
- MA50 排列：MA20 > MA50 时 +15 分
- 时效性：当日信号（ago=0）得满分，历史信号递减（0.7x, 0.4x）
- 接近历史新高：距 ATH 3% 以内（无上方阻力）最多 +25 分
- 周线完全排列、日线均线发散、阳线加分

**参数：** `d_xfast`, `d_fast`, `d_mid`, `d_slow`, `w_fast`, `w_mid`, `approach_pct`, `touch_pct`, `lookback`, `wick_body_ratio`, `upper_wick_max`

### `strong_pullback` -- 强势周线趋势 + 日线回踩

寻找周线趋势强劲（周线收盘 > wMA10 > wMA20 > wMA40）且日线回踩 MA10/MA20 后以阳线反弹的股票。日线趋势要求 MA5 > MA10 > MA20，MA20 > MA50 时额外加 +15 分。

**参数：** `d_xfast`, `d_fast`, `d_mid`, `d_slow`, `w_fast`, `w_mid`, `w_slow`, `lookback_days`, `touch_pct`, `min_align_days`

### `ma_pullback` -- 均线排列 + 回踩

寻找日线 5/10/20 均线多头排列、且价格回踩至 5 均线 2% 以内的股票。MA20 > MA50 排列时额外加 +15 分。

**参数：** `ma_short`, `ma_medium`, `ma_long`, `ma_trend`, `pullback_pct`, `min_trend_days`

## 添加新分析器

在 `scanners/` 目录下创建文件即可，系统自动发现，无需修改其他文件。

```python
# scanners/my_analyzer.py
from typing import Optional
import pandas as pd
from scanners.base import BaseScanner, ScanResult, resample_ohlcv
from scanners.registry import register

@register
class MyAnalyzer(BaseScanner):
    name = "my_analyzer"
    description = "在 list-analyzers 中显示的简短描述"

    def scan(self, ticker: str, ohlcv: pd.DataFrame, fundamentals: pd.Series) -> Optional[ScanResult]:
        # ohlcv: 日线 OHLCV，DatetimeIndex，列 [Open, High, Low, Close, Volume]
        # 用 resample_ohlcv(ohlcv, 'W') 转周线，'ME' 转月线

        close = ohlcv["Close"]
        # ... 你的逻辑 ...

        return ScanResult(
            ticker=ticker,
            score=75.0,         # 0-100
            signal="BUY",       # STRONG_BUY / BUY / WATCH
            details={"close": round(close.iloc[-1], 2)},
        )
```

然后运行：`uv run python main.py analyze -s my_analyzer`

## 项目结构

```
main.py                 CLI 入口
config.py               路径与常量配置
pyproject.toml          依赖与项目元信息（由 uv 管理）
uv.lock                 锁定的依赖版本
tickers/
  universe.py           通过 yfinance 筛选器获取股票池
data/
  ohlcv_cache.py        按股票缓存 Parquet，增量拉取
  fundamentals_cache.py 基本面缓存（单文件，每日刷新）
scanners/
  base.py               BaseScanner 抽象类、ScanResult、模拟数据类、resample_ohlcv
  registry.py           通过 @register 装饰器自动发现分析器
  ma_pullback.py        均线排列 + 回踩分析器
  strong_pullback.py    强势周线趋势 + 日线反弹分析器
  entry_point.py        趋势入场点分析器（触及/锤子线识别，自定义出场规则）
simulation/
  engine.py             逐股票逐日交易模拟器（SimulationEngine）
  portfolio.py          组合级别模拟器，共享资金池（PortfolioEngine）
backtest/
  ma_sensitivity.py     均线触及回测引擎（bounce + max_return 策略）
output/
  formatter.py          Rich 终端表格 + CSV 导出（analyze/backtest）
  simulator_formatter.py  逐股票模拟汇总表、交易记录、CSV/权益曲线导出
  portfolio_formatter.py  组合模拟汇总、交易记录、按股票统计、CSV 导出
```

## 数据存储

所有数据缓存在 `data/` 目录下：

- `data/tickers.parquet` -- 股票池
- `data/ohlcv/{TICKER}.parquet` -- 每只股票的日线 OHLCV
- `data/fundamentals.parquet` -- 所有股票的基本面数据
- `results/` -- `--csv` 导出结果（analyze/、simulation/、portfolio/）
