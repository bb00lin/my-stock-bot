import yfinance as yf
import pandas as pd
import pandas_ta as ta
from FinMind.data import DataLoader
import requests

# ================= 🔐 安全設定區 =================
# 請填入你刚才找到的代碼
LINE_ACCESS_TOKEN = os.getenv('LINE_ACCESS_TOKEN')
LINE_USER_ID = os.getenv('LINE_USER_ID')

# 你想讓機器人每天巡邏的股票清單 (可自由增減)
WATCH_LIST = ["2330.TW", "2317.TW", "2454.TW", "0050.TW", "2303.TW", "2603.TW"]
# ================================================

def get_expert_signal(ticker_symbol):
    """分析單一股票並判斷是否發送訊號"""
    try:
        # 1. 抓取技術面
        stock = yf.Ticker(ticker_symbol)
        df = stock.history(period="1y")
        if df.empty: return None
        
        # 計算 MA5 與 RSI
        df['MA5'] = ta.sma(df['Close'], length=5)
        df['RSI'] = ta.rsi(df['Close'], length=14)
        now = df.iloc[-1]
        
        # 2. 抓取籌碼面 (外資近5日累積動向)
        stock_id = ticker_symbol.replace(".TW", "")
        dl = DataLoader()
        start_dt = (pd.Timestamp.now() - pd.Timedelta(days=10)).strftime('%Y-%m-%d')
        df_chip = dl.taiwan_stock_institutional_investors(stock_id=stock_id, start_date=start_dt)
        chip_sum = df_chip.groupby('name').sum(numeric_only=True)
        foreign = int((chip_sum.loc['Foreign_Investor', 'buy'] - chip_sum.loc['Foreign_Investor', 'sell']) / 1000)
        
        # 3. 達人篩選準則 (自定義：外資買超 + 股價站上MA5 + RSI未過熱)
        if foreign > 500 and now['Close'] > now['MA5'] and now['RSI'] < 75:
            yoy = stock.info.get('revenueGrowth', 0) * 100
            msg = (
                f"\n🎯 【{ticker_symbol} 買進訊號】\n"
                f"● 當前價格: {now['Close']:.2f}\n"
                f"● 外資加碼: {foreign} 張\n"
                f"● RSI位階: {now['RSI']:.2f}\n"
                f"● 營收YoY: {yoy:.1f}%\n"
                f"💡 觀點：主力進場且股價強勢，建議關注。"
            )
            return msg
    except Exception as e:
        print(f"分析 {ticker_symbol} 時出錯: {e}")
        return None
    return None

def send_to_line(text):
    """透過 LINE Messaging API 發送推播"""
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text}]
    }
    r = requests.post(url, headers=headers, json=payload)
    return r.status_code

# --- 機器人執行流程 ---
print("🤖 Bob 股票機器人啟動中，正在掃描清單...")
final_report = "📊 今日強勢股篩選報告：\n"
found_flag = False

for ticker in WATCH_LIST:
    print(f"正在檢查 {ticker}...")
    signal = get_expert_signal(ticker)
    if signal:
        final_report += signal + "\n"
        found_flag = True

if found_flag:
    status = send_to_line(final_report)
    if status == 200:
        print("✅ 成功！請查看手機 LINE 訊息。")
    else:
        print(f"❌ 發送失敗，錯誤代碼: {status}。請檢查 Token 是否正確。")
else:
    print("😴 今日觀察名單中暫無強勢訊號。")
