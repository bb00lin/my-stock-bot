import os
import yfinance as yf
import pandas as pd
import requests
import datetime
import time
import sys
import subprocess
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator

# ==========================================
# 1. 環境設定 (LINE 額度已滿，僅保留結構)
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

def save_and_verify_report(content):
    """
    強制存檔至 D:\Mega\下載\個股
    並排除路徑斜線混用問題
    """
    # 1. 定義路徑 (確保使用原始字串)
    base_dir = r"D:\Mega\下載\個股"
    
    # 2. 強制檢查並建立路徑
    if not os.path.exists(base_dir):
        try:
            os.makedirs(base_dir)
            print(f"📂 已成功建立資料夾: {base_dir}")
        except Exception as e:
            print(f"❌ 無法建立 D 槽路徑，改存至 C 槽桌面。錯誤: {e}")
            base_dir = os.path.join(os.path.expanduser("~"), "Desktop")

    # 3. 組合檔名並標準化路徑 (解決 / 與 \ 混用)
    date_str = datetime.date.today().strftime('%Y-%m-%d')
    filename = f"Stock_Report_{date_str}.txt"
    full_path = os.path.normpath(os.path.join(base_dir, filename))
    
    try:
        # 4. 強制寫入
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        # 5. 二次確認檔案是否存在
        if os.path.exists(full_path):
            print("-" * 30)
            print(f"✅ 存檔成功！")
            print(f"📍 檔案位置: {full_path}")
            print(f"📏 檔案大小: {os.path.getsize(full_path)} bytes")
            print("-" * 30)
            
            # 6. 強制開啟資料夾並選中該檔案 (Windows 專用)
            subprocess.Popen(f'explorer /select,"{full_path}"')
        else:
            print("❌ 存檔失敗：檔案在寫入後消失了 (可能是被防毒或同步軟體攔截)。")
            
    except Exception as e:
        print(f"❌ 發生存檔異常：{e}")

# ==========================================
# 2. 核心診斷邏輯
# ==========================================
def get_diagnostic_report(sid):
    try:
        clean_id = str(sid).split('.')[0].strip()
        # 獲取名稱
        dl = DataLoader()
        df_info = dl.taiwan_stock_info()
        target = df_info[df_info['stock_id'] == clean_id]
        stock_name = target.iloc[0]['stock_name'] if not target.empty else "個股"
        
        # 股價
        df = yf.Ticker(f"{clean_id}.TW").history(period="1y")
        if df.empty: df = yf.Ticker(f"{clean_id}.TWO").history(period="1y")
        if df.empty: return f"❌ {clean_id}: 找不到資料"

        curr_p = df.iloc[-1]['Close']
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        bias = ((curr_p - ma60) / ma60) * 100
        
        report = (
            f"【{clean_id} {stock_name}】\n"
            f" 現價:{curr_p:.2f} | 乖離:{bias:+.1f}%\n"
            f" ------------------------------------"
        )
        return report
    except Exception as e:
        return f"❌ {sid} 錯誤: {e}"

# ==========================================
# 3. 執行
# ==========================================
if __name__ == "__main__":
    # 支援輸入: python ManualStock.py "2344 0052"
    input_str = sys.argv[1] if len(sys.argv) > 1 else "2344"
    targets = input_str.replace(',', ' ').split()
    
    print(f"🚀 啟動掃描...")
    results = [get_diagnostic_report(t.strip().upper()) for t in targets]
    
    final_output = f"📊 診斷報告 ({datetime.date.today()})\n" + "="*30 + "\n" + "\n".join(results)
    
    # 儲存並開啟
    save_and_verify_report(final_output)
