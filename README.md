# InvestmentUSData

从 [Tiingo](https://www.tiingo.com/) 拉取美股日线，支持增量更新、多 API Key 轮换；可用 **GitHub Actions** 定时跑任务并回推 `tickers/`、`tickers_A股ETF/` 下的 CSV。

## 本地

需 **Python 3.11+**。

```bash
cd InvestmentUSData
python3 -m venv .venv && source .venv/bin/activate   # 可选
pip install -r requirements.txt
cp config.example.py config.py
# 编辑 config.py 填入 Tiingo Token
python download.py
```

单标的（须在 `ticker.csv` 中）：`python -c "from download import download_ticker; download_ticker('SPY')"`

**配置**：本地用 `config.py`（勿提交）；CI 用 Secret **`TIINGO_API_KEY`** 或 **`TIINGO_API_KEYS`**。

## 下载本仓库已有的股票数据

**`tickers/`** 平铺到 **`${HOME}/us_data`**，**`tickers_A股ETF/`** 平铺到 **`${HOME}/cn_etf_data`**：

```bash
US_DATA="${HOME}/us_data"
CN_ETF_DATA="${HOME}/cn_etf_data"
REPO="https://github.com/linjianz/InvestmentUSData.git"
BRANCH="${BRANCH:-main}"
WORKDIR="$(mktemp -d)"
git clone --depth=1 --filter=blob:none --sparse -b "$BRANCH" "$REPO" "$WORKDIR/repo"
git -C "$WORKDIR/repo" sparse-checkout set tickers tickers_A股ETF
mkdir -p "$US_DATA" "$CN_ETF_DATA"
rsync -a --delete "$WORKDIR/repo/tickers/" "$US_DATA/"
[ -d "$WORKDIR/repo/tickers_A股ETF" ] && rsync -a "$WORKDIR/repo/tickers_A股ETF/" "$CN_ETF_DATA/"
rm -rf "$WORKDIR"
```

## GitHub Actions

工作流：[`.github/workflows/daily-download.yml`](.github/workflows/daily-download.yml)。

1. 默认分支 **`main`**（或改 workflow 里的分支）。
2. Actions Secret：**`TIINGO_API_KEYS`**（多 Key 逗号分隔）。
3. **Settings → Actions → General → Workflow permissions**：勾选 **Read and write permissions**（否则无法 push CSV）。

**触发**：工作日北京时间约 02:00、10:00（UTC cron）；也可 Actions 里 **Run workflow**。有变更会提交，说明中含 `[skip ci]`。

## 其它

- **勿提交**含真实 Key 的 `config.py`；泄露请在 Tiingo 后台轮换 Token 并更新 Secrets。
