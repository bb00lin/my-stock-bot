name: DailyStockPush

on:
  schedule:
    - cron: '30 6 * * 1-5' # 台北 14:30
    - cron: '10 8 * * 1-5' # 台北 16:10
  workflow_dispatch: 

permissions:
  contents: read

jobs:
  run_job:
    runs-on: ubuntu-latest
    timeout-minutes: 45
    steps:
      - name: Checkout Code
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'

      - name: Install Dependencies
        run: |
          # [關鍵] 安裝 google-genai (新版 SDK) 
          # 同時保留 google-generativeai (舊版) 以防 DailyStockBot 還需要它
          pip install -U tqdm requests pandas yfinance FinMind gspread oauth2client google-genai google-generativeai

      - name: Create Key File
        run: echo '${{ secrets.GOOGLE_SHEETS_JSON }}' > google_key.json

      - name: Run DailyStockPush
        env:
          LINE_ACCESS_TOKEN: ${{ secrets.LINE_ACCESS_TOKEN }}
          LINE_USER_ID: ${{ secrets.LINE_USER_ID }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        run: python DailyStockPush.py

      - name: Cleanup Key
        if: always()
        run: rm -f google_key.json
