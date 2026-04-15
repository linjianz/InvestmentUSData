# 复制为 config.py 并填入你的 Tiingo API Key（config.py 已在 .gitignore 中，勿提交）
# 获取地址: https://www.tiingo.com/account/api/token
# 支持多 Key 轮换限流（单账号每小时 50 次）
# GitHub Actions：在仓库 Secrets 中设置 TIINGO_API_KEYS（逗号分隔多个 key）

TIINGO_CONFIG = [
    {'api_key': 'YOUR_TIINGO_API_KEY', 'session': True},
]
