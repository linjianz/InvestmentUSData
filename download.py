"""
数据下载模块 - 从 Tiingo/FRED 下载股票数据
支持增量下载，自动切换 API Key
"""
import os
import shutil
import sys
import pandas as pd
from datetime import date
from tiingo import TiingoClient
from pandas.tseries.offsets import BDay
import requests
from tqdm import tqdm
from typing import Optional, List

PROJECT_DIR = os.path.dirname(__file__)
sys.path.append(PROJECT_DIR)


START_DATE: str = '1980-01-01'


def _load_tiingo_config():
    """优先从环境变量读取（CI/GitHub Actions），否则从 config.py 读取（本地）"""
    keys_str = os.environ.get('TIINGO_API_KEYS') or os.environ.get('TIINGO_API_KEY')
    if keys_str:
        keys = [k.strip() for k in keys_str.split(',') if k.strip()]
        if keys:
            return [{'api_key': k, 'session': True} for k in keys]
    try:
        from config import TIINGO_CONFIG
        return TIINGO_CONFIG
    except ImportError:
        pass
    raise ValueError(
        '请配置 TIINGO_API_KEY 环境变量（或 TIINGO_API_KEYS 逗号分隔多 key），'
        '或在项目根目录创建 config.py，参考 config.example.py'
    )


TIINGO_CONFIG = _load_tiingo_config()


DATA_DIR = os.path.join(PROJECT_DIR, 'tickers')
A_SHARE_ETF_DIR = os.path.join(PROJECT_DIR, 'tickers_A股ETF')
A_SHARE_ETF_MARKET = 'A股ETF'
TICKER_CSV_PATH = os.path.join(PROJECT_DIR, 'ticker.csv')

_current_config_index = 0


def _get_current_client() -> TiingoClient:
    global _current_config_index
    return TiingoClient(TIINGO_CONFIG[_current_config_index])


def _switch_to_next_config() -> bool:
    global _current_config_index
    if _current_config_index < len(TIINGO_CONFIG) - 1:
        _current_config_index += 1
        print(f"\n⚠️  API 限流，切换到第 {_current_config_index + 1} 个配置")
        return True
    return False


def _is_rate_limit_error(error_msg: str) -> bool:
    rate_limit_keywords = [
        'hourly request allocation',
        'run over your',
        'rate limit',
        'too many requests'
    ]
    error_lower = error_msg.lower()
    return any(keyword in error_lower for keyword in rate_limit_keywords)


def _check_if_update_needed(last_date: pd.Timestamp, end_date: str) -> tuple:
    current_date = pd.to_datetime(end_date)

    if last_date.date() >= current_date.date():
        return False, ''

    new_start_date = (last_date + BDay(1)).strftime('%Y-%m-%d')

    if pd.to_datetime(new_start_date).date() >= current_date.date():
        return False, ''

    return True, new_start_date


def _data_dir_for_market(market) -> str:
    """A股ETF 输出到 tickers_A股ETF，其余与历史行为一致为 tickers。"""
    if pd.isna(market):
        return DATA_DIR
    if str(market).strip() == A_SHARE_ETF_MARKET:
        return A_SHARE_ETF_DIR
    return DATA_DIR


def _handle_incremental_download(ticker: str, file_path: str, start_date: str,
                                 end_date: str) -> dict:
    date_format = '%Y-%m-%d %H:%M:%S'

    try:
        existing_df = pd.read_csv(
            file_path,
            parse_dates=['date'],
            dayfirst=False,
            index_col=False
        )
    except Exception as e:
        return {'status': 'failed', 'count': 0, 'error': f'读取文件失败: {str(e)}', 'last_date': None}

    if len(existing_df) == 0:
        data, error = _download_data(ticker, start_date, end_date, date_format)
        if error:
            return {'status': 'failed', 'count': 0, 'error': error, 'last_date': None}
        if not data.empty:
            data.to_csv(file_path, index=False)
            last_date_str = data['date'].iloc[-1]
            return {'status': 'reloaded', 'count': len(data), 'error': None, 'last_date': last_date_str}
        return {'status': 'reloaded', 'count': 0, 'error': None, 'last_date': None}

    if 'date' in existing_df.columns:
        existing_df['date'] = pd.to_datetime(existing_df['date']).dt.strftime(date_format)

    last_date = pd.to_datetime(existing_df['date'].values[-1])
    last_date_str = last_date.strftime(date_format)
    need_update, new_start_date = _check_if_update_needed(last_date, end_date)

    if not need_update:
        return {'status': 'up_to_date', 'count': 0, 'error': None, 'last_date': last_date_str}

    new_data, error = _download_data(ticker, new_start_date, end_date, date_format)
    if error:
        return {'status': 'failed', 'count': 0, 'error': error, 'last_date': last_date_str}

    if not new_data.empty:
        updated_df = pd.concat([existing_df, new_data], axis=0, ignore_index=True)
        updated_df.to_csv(file_path, index=False)
        new_last_date_str = new_data['date'].iloc[-1]
        return {'status': 'updated', 'count': len(new_data), 'error': None, 'last_date': new_last_date_str}
    else:
        return {'status': 'no_new_data', 'count': 0, 'error': None, 'last_date': last_date_str}


def download_ticker(ticker: str = '', exclude_markets: Optional[List[str]] = None):
    """
    下载股票数据，支持增量下载

    Args:
        ticker: 股票代码，未指定则下载 ticker.csv 中的所有股票数据
               如果指定了，必须位于 ticker.csv 中
        exclude_markets: 需要排除的市场列表，比如 ["H股"]
    """
    if not os.path.exists(TICKER_CSV_PATH):
        raise FileNotFoundError(f'ticker.csv 文件不存在: {TICKER_CSV_PATH}')

    ticker_info_df = pd.read_csv(TICKER_CSV_PATH)

    if exclude_markets:
        ticker_info_df = ticker_info_df[~ticker_info_df['market'].isin(exclude_markets)]

    valid_tickers = ticker_info_df['ticker'].tolist()

    if not ticker:
        ticker_name_map = dict(zip(ticker_info_df['ticker'], ticker_info_df['name']))
        ticker_market_map = dict(zip(ticker_info_df['ticker'], ticker_info_df['market']))

        status_map = {
            'first_download': '首次下载',
            'reloaded': '重新下载',
            'up_to_date': '已是最新',
            'updated': '增量更新',
            'no_new_data': '无新数据',
            'no_data': '无数据',
            'failed': '失败'
        }

        results = []
        pbar = tqdm(valid_tickers, desc='下载进度', unit='ticker')
        for t in pbar:
            result = _download_single_ticker(t, ticker_market_map.get(t))
            result['ticker'] = t
            result['name'] = ticker_name_map.get(t, t)
            results.append(result)
            status = status_map.get(result['status'], result['status'])
            count = result['count']
            pbar.set_description(f'下载进度 [{t}]')
            pbar.set_postfix({'状态': status, '条数': count})

        _print_summary(results)
        return

    if ticker not in valid_tickers:
        raise ValueError(f'ticker "{ticker}" 不在 ticker.csv 中（或被 exclude_markets 过滤）。有效的 ticker 列表: {valid_tickers}')

    row = ticker_info_df[ticker_info_df['ticker'] == ticker].iloc[0]
    result = _download_single_ticker(ticker, row['market'])
    if result.get('status') == 'failed':
        print(f"下载 {ticker} 失败: {result.get('error', '未知错误')}")


def _get_display_name(result: dict) -> str:
    name = result.get('name', result['ticker'])
    if pd.isna(name) or (isinstance(name, str) and name.lower() == 'nan'):
        return result['ticker']
    return name


def _print_summary(results: list):
    tickers_count = len(results)
    print('\n' + '=' * 30)
    print(f'共{tickers_count}只股票')
    print('=' * 30)

    up_to_date_tickers = [r for r in results if r['status'] == 'up_to_date']
    updated_tickers = [r for r in results if r['status'] in ['updated', 'first_download', 'reloaded']]
    failed_tickers = [r for r in results if r['status'] == 'failed']
    no_new_data_tickers = [r for r in results if r['status'] == 'no_new_data']
    no_data_tickers = [r for r in results if r['status'] == 'no_data']

    print(f"1. {len(up_to_date_tickers)} 只股票无需更新 (已是最新)")

    print(f"2. {len(updated_tickers)} 只股票更新成功")
    if updated_tickers:
        for r in updated_tickers:
            status_map = {
                'first_download': '首次下载',
                'reloaded': '重新下载',
                'updated': '增量更新'
            }
            status_name = status_map.get(r['status'], r['status'])
            print(f"   {_get_display_name(r)} ({r['ticker']}): {status_name}, {r['count']} 条数据")

    print(f"3. {len(no_new_data_tickers)} 只股票检查后无新数据")

    print(f"4. {len(no_data_tickers)} 只股票无数据")

    print(f"5. {len(failed_tickers)} 只股票更新失败")
    if failed_tickers:
        for r in failed_tickers:
            error_msg = r.get('error', '未知错误')
            print(f"   {_get_display_name(r)} ({r['ticker']}): {error_msg}")

    print('=' * 30)
    valid_results = [r for r in results if r.get('last_date')]
    if valid_results:
        max_dt = max(pd.to_datetime(r['last_date']) for r in valid_results)
        max_date_str = max_dt.strftime('%Y-%m-%d')

        latest_tickers = [r for r in valid_results if pd.to_datetime(r['last_date']).date() == max_dt.date()]
        latest_names = [_get_display_name(r) for r in latest_tickers]
        latest_names_str = ", ".join(latest_names)

        print(f"获取到的最新日期: {max_date_str}")
        print(f"拥有最新日期的标的: {latest_names_str}")

        outliers = []
        for r in valid_results:
            if pd.to_datetime(r['last_date']).date() < max_dt.date():
                outliers.append(r)

        if outliers:
            print(f"\n以下 {len(outliers)} 只股票数据日期落后于最新日期:")
            outliers.sort(key=lambda x: x['last_date'])
            for r in outliers:
                date_str = r['last_date'].split(' ')[0] if r['last_date'] else 'Unknown'
                print(f"   {_get_display_name(r)} ({r['ticker']}): {date_str}")
        else:
            print("所有股票数据日期一致")
    else:
        print("未获取到任何有效日期数据")

    print('=' * 30)


def _download_single_ticker(ticker: str, market=None) -> dict:
    data_dir = _data_dir_for_market(market)
    os.makedirs(data_dir, exist_ok=True)
    start_date = START_DATE
    end_date = date.today().strftime('%Y-%m-%d')
    file_path = os.path.join(data_dir, f'{ticker}.csv')
    legacy_path = os.path.join(DATA_DIR, f'{ticker}.csv')
    if data_dir != DATA_DIR and not os.path.exists(file_path) and os.path.exists(legacy_path):
        shutil.move(legacy_path, file_path)
    date_format = '%Y-%m-%d %H:%M:%S'

    try:
        if os.path.exists(file_path):
            return _handle_incremental_download(ticker, file_path, start_date, end_date)
        else:
            data, error = _download_data(ticker, start_date, end_date, date_format)
            if error:
                return {'status': 'failed', 'count': 0, 'error': error, 'last_date': None}
            if not data.empty:
                data.to_csv(file_path, index=False)
                last_date_str = data['date'].iloc[-1]
                return {'status': 'first_download', 'count': len(data), 'error': None, 'last_date': last_date_str}
            else:
                return {'status': 'no_data', 'count': 0, 'error': None, 'last_date': None}
    except Exception as e:
        return {'status': 'failed', 'count': 0, 'error': str(e), 'last_date': None}


def _download_data(ticker: str, start_date: str, end_date: str,
                   date_format: str = '%Y-%m-%d %H:%M:%S') -> tuple:
    if ticker == 'VIX':
        df, error = _download_vix_data(start_date, end_date, date_format)
        return df, error

    max_attempts = len(TIINGO_CONFIG)

    for attempt in range(max_attempts):
        try:
            client = _get_current_client()
            data = client.get_ticker_price(
                ticker=ticker,
                fmt='json',
                startDate=start_date,
                endDate=end_date,
                frequency='daily'
            )

            if not data:
                return pd.DataFrame(), None

            df = pd.DataFrame(data)

            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date']).dt.strftime(date_format)

            expected_columns = [
                'date', 'close', 'high', 'low', 'open', 'volume',
                'adjClose', 'adjHigh', 'adjLow', 'adjOpen', 'adjVolume',
                'divCash', 'splitFactor'
            ]
            df = df.reindex(columns=[col for col in expected_columns if col in df.columns])

            return df, None

        except Exception as e:
            error_msg = str(e)

            if _is_rate_limit_error(error_msg):
                if attempt < max_attempts - 1 and _switch_to_next_config():
                    continue
                else:
                    error_msg = "所有 API 配置都已达到限流上限，请稍后再试"
                    return pd.DataFrame(), error_msg

            if '404' in error_msg or 'not found' in error_msg.lower():
                error_msg = f"Ticker '{ticker}' not found"
            return pd.DataFrame(), error_msg

    return pd.DataFrame(), "下载失败：已达到最大重试次数"


def _normalize_vix_df(df: pd.DataFrame, date_format: str) -> pd.DataFrame:
    """将 VIX 数据统一为标准列格式，数值保留 2 位小数"""
    df = df.copy()
    df['high'] = df['close']
    df['low'] = df['close']
    df['open'] = df['close']
    df['volume'] = 0
    df['adjClose'] = df['close']
    df['adjHigh'] = df['close']
    df['adjLow'] = df['close']
    df['adjOpen'] = df['close']
    df['adjVolume'] = 0
    df['divCash'] = 0.0
    df['splitFactor'] = 1.0
    df['date'] = pd.to_datetime(df['date']).dt.strftime(date_format)
    # 数值列保留 2 位小数，与 FRED 格式一致
    numeric_cols = [
        'close', 'high', 'low', 'open', 'adjClose', 'adjHigh', 'adjLow', 'adjOpen',
        'adjVolume', 'divCash', 'splitFactor',
    ]
    for col in numeric_cols:
        df[col] = df[col].round(2)
    expected_columns = [
        'date', 'close', 'high', 'low', 'open', 'volume',
        'adjClose', 'adjHigh', 'adjLow', 'adjOpen', 'adjVolume',
        'divCash', 'splitFactor'
    ]
    return df[expected_columns]


def _download_vix_from_yahoo(start_date: str, end_date: str, date_format: str) -> tuple:
    """Yahoo Finance 通常比 FRED 更新更及时，用于补充缺失的最近交易日"""
    try:
        import yfinance as yf
        end_plus = (pd.to_datetime(end_date) + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        data = yf.download('^VIX', start=start_date, end=end_plus, progress=False, auto_adjust=False)
        if data.empty:
            return pd.DataFrame(), None
        # 处理 MultiIndex 列（yfinance 新版本）
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        df = data.reset_index()
        close_col = 'Close' if 'Close' in df.columns else 'close'
        if close_col not in df.columns:
            return pd.DataFrame(), None
        date_col = 'Date' if 'Date' in df.columns else 'date'
        df = df[[date_col, close_col]].rename(columns={date_col: 'date', close_col: 'close'})
        return _normalize_vix_df(df, date_format), None
    except Exception as e:
        return pd.DataFrame(), str(e)


def _download_vix_data(
    start_date: str, end_date: str, date_format: str = '%Y-%m-%d %H:%M:%S'
) -> tuple:
    """优先 Yahoo（更新及时），FRED 延迟则补充或回退"""
    end_dt = pd.to_datetime(end_date)

    # 1. 优先 Yahoo，通常当日收盘后几小时内即有数据
    yahoo_df, _ = _download_vix_from_yahoo(start_date, end_date, date_format)
    if not yahoo_df.empty:
        last = pd.to_datetime(yahoo_df['date'].iloc[-1]).date()
        if last >= end_dt.date():
            return yahoo_df, None

    # 2. FRED
    try:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS&cosd={start_date}&coed={end_date}"
        response = requests.get(url)
        response.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(response.text))
        if df.empty:
            return (yahoo_df, None) if not yahoo_df.empty else (pd.DataFrame(), None)

        if 'DATE' in df.columns and 'VIXCLS' in df.columns:
            df.rename(columns={'DATE': 'date', 'VIXCLS': 'close'}, inplace=True)
        elif len(df.columns) >= 2:
            df.columns = ['date', 'close']
        df['date'] = pd.to_datetime(df['date'])
        start_dt = pd.to_datetime(start_date)
        df = df[(df['date'] >= start_dt) & (df['date'] <= end_dt)]

        if not yahoo_df.empty:
            yahoo_df = yahoo_df.copy()
            yahoo_df['date'] = pd.to_datetime(yahoo_df['date'])
            df = pd.concat([df, yahoo_df], ignore_index=True)
            df = df.drop_duplicates(subset=['date'], keep='last').sort_values('date')

        return _normalize_vix_df(df, date_format), None
    except Exception as e:
        if not yahoo_df.empty:
            return yahoo_df, None
        return pd.DataFrame(), str(e)


if __name__ == '__main__':
    download_ticker()
