# InvestmentUSData

从 [Tiingo](https://www.tiingo.com/) 拉取美股日线数据，支持增量更新与多 API Key 轮换；可通过 **GitHub Actions** 定时跑任务并把 `tickers/` 里的 CSV 自动提交回仓库。

## 功能概览

- **标的列表**：根目录 `ticker.csv`（列：`ticker`, `market`, `name`）
- **输出**：每个标的一个文件 `tickers/<代码>.csv`（Tiingo 日频字段：`date`, `close`, `high`, `low`, `open`, `volume`, 复权与分红拆分相关列等）
- **增量**：已有 CSV 时只请求缺失区间，减少 API 调用
- **限流**：环境变量 `TIINGO_API_KEYS` 支持逗号分隔多个 Key，遇限流自动切换
- **VIX**：代码中对 `VIX` 有特殊处理（Yahoo / FRED）；当前默认列表以股票 ETF 为主，可按需在 `ticker.csv` 中加入

## 本地运行

需要 **Python 3.11+**（与 CI 一致即可）。

```bash
cd InvestmentUSData
python3 -m venv .venv && source .venv/bin/activate   # 可选
pip install -r requirements.txt
cp config.example.py config.py
# 编辑 config.py，填入 Tiingo API Token（https://www.tiingo.com/account/api/token）
python download.py
```

仅下载某一个标的（且该标的必须在 `ticker.csv` 中）：

```bash
python -c "from download import download_ticker; download_ticker('SPY')"
```

## 从仓库同步 CSV（本地）

无需 Tiingo：从本仓库拉取已由 Actions 更新的 `tickers/` 下 CSV，并**平铺**到 **`${HOME}/us_data`**（各标的一个 `*.csv` 文件）。

**手动下载（稀疏克隆，只拉 `tickers/` 下的 CSV，再平铺到 `us_data`）：**

```bash
US_DATA="${HOME}/us_data"
REPO="https://github.com/linjianz/InvestmentUSData.git"
BRANCH="${BRANCH:-main}"
WORKDIR="$(mktemp -d)"
git clone --depth=1 --filter=blob:none --sparse -b "$BRANCH" "$REPO" "$WORKDIR/repo"
git -C "$WORKDIR/repo" sparse-checkout set tickers
mkdir -p "$US_DATA"
rsync -a --delete "$WORKDIR/repo/tickers/" "$US_DATA/"
rm -rf "$WORKDIR"
```

需要本机已安装 **git** 与 **rsync**。若使用 fork，将 `REPO` 改为你的仓库地址即可。

### 配置方式（二选一）

| 场景 | 做法 |
|------|------|
| 本地 | 使用 `config.py`（已从 `.gitignore` 排除，**勿提交**） |
| CI / Shell | 设置环境变量 `TIINGO_API_KEY` 或 `TIINGO_API_KEYS`（多 Key 用英文逗号分隔） |

## GitHub Actions

工作流文件：[`.github/workflows/daily-download.yml`](.github/workflows/daily-download.yml)。

1. 将本仓库默认分支设为 **`main`**（或同步修改 workflow 中的 `ref` / `git push` 目标分支）。
2. 打开 **Settings → Secrets and variables → Actions**，新建 Secret：
   - 名称：`TIINGO_API_KEYS`
   - 值：一个或多个 Tiingo Key，多个时用英文逗号连接（与本地 `TIINGO_API_KEYS` 行为一致）。
3. 赋予 Actions 写仓库权限：**Settings → Actions → General → Workflow permissions**，勾选 **Read and write permissions**（否则无法 `git push` 更新 `tickers/`）。

触发方式：

- **定时**：工作日北京时间约 **02:00**、**10:00**（workflow 内用多条 UTC `cron` 等价表达）。GitHub Actions 可能排队延迟，实际运行时间仅供参考。
- **手动**：在 Actions 页面选择 **Daily Data Download** → **Run workflow**。

有数据变更时会自动提交，提交说明中含 `[skip ci]`，避免推送再次触发同一流水线。

## 目录结构

```
.
├── download.py           # 下载与增量逻辑
├── ticker.csv            # 标的清单
├── tickers/              # 各标的 CSV（可提交或由 CI 更新）
├── config.example.py     # 本地配置模板（可安全提交）
├── config.py             # 本地密钥（勿提交，见 .gitignore）
├── requirements.txt
└── .github/workflows/daily-download.yml
```

## 安全说明

- **永远不要**将含真实 Key 的 `config.py` 提交到 Git。
- 若 Key 曾误提交或泄露，请在 Tiingo 后台 **轮换 Token** 并更新本地与 GitHub Secrets。

## 依赖

见 [`requirements.txt`](requirements.txt)：`pandas`、`tiingo`、`requests`、`tqdm`、`yfinance`（用于 VIX 等补充数据源）。
