import os
import json
import requests
import pandas as pd
import gspread
from datetime import datetime
import time

# ===================================================================
# V5.1 偵錯版：在每個關鍵步驟都增加詳細的日誌輸出
# ===================================================================

print("--- main.py 腳本開始執行 ---")

# ===================================================================
# 1. 從 GitHub Secrets 讀取安全憑證
# ===================================================================
print("\nSTEP 1: 正在從環境變數讀取憑證...")
try:
    dify_api_key = os.environ['DIFY_API_KEY']
    dify_api_url = os.environ['DIFY_API_URL']
    google_creds_json = os.environ['GOOGLE_SHEETS_CREDENTIALS']
    print("✅ 成功讀取環境變數。")

    print("  - 正在解析 Google Sheets JSON 憑證...")
    google_creds_dict = json.loads(google_creds_json)
    print("  - ✅ Google Sheets JSON 憑證解析成功。")

except KeyError as e:
    print(f"❌ 致命錯誤：在 GitHub Secrets 中找不到必要的憑證：{e}")
    exit(1)
except json.JSONDecodeError as e:
    print(f"❌ 致命錯誤：GOOGLE_SHEETS_CREDENTIALS 的內容不是一個有效的 JSON 格式。請檢查貼上的內容是否完整。錯誤：{e}")
    exit(1)

# ===================================================================
# 2. 设定 Google Sheets 连接
# ===================================================================
print("\nSTEP 2: 正在連接到 Google Sheets...")
try:
    gc = gspread.service_account_from_dict(google_creds_dict)
    print("  - ✅ gspread 服務帳號初始化成功。")

    spreadsheet_name = "App 評論自動化洞察系統"
    print(f"  - 正在開啟試算表檔案: '{spreadsheet_name}'...")
    spreadsheet = gc.open(spreadsheet_name) 
    print("  - ✅ 試算表檔案開啟成功。")

    worksheet_name = "評論資料庫 (Reviews_DB)"
    print(f"  - 正在開啟工作表分頁: '{worksheet_name}'...")
    worksheet = spreadsheet.worksheet(worksheet_name)
    print("  - ✅ 工作表分頁開啟成功。")
    
    print(f"✅ 成功連接到 Google Sheet: '{spreadsheet_name}' -> Worksheet: '{worksheet_name}'")
except gspread.exceptions.SpreadsheetNotFound:
    print(f"❌ 致命錯誤: 找不到名為 '{spreadsheet_name}' 的 Google Sheet 檔案。請確認名稱完全正確，且服務帳號已被授予該檔案的編輯權限。")
    exit(1)
except gspread.exceptions.WorksheetNotFound:
    print(f"❌ 致命錯誤: 在試算表中找不到名為 '{worksheet_name}' 的分頁。")
    exit(1)
except Exception as e:
    print(f"❌ 連接 Google Sheets 時發生未知錯誤: {e}")
    exit(1)

# ===================================================================
# 3. 定义 Dify AI 分析功能 (这部分代码不变，先省略)
# ... 完整的 Dify, 爬蟲, 和主流程代码 ...
# 为了避免混淆，这里贴上完整的代码
# ===================================================================

from app_store_scraper import AppStore
from google_play_scraper import Sort, reviews

def analyze_with_dify(comment):
    headers = {"Authorization": f"Bearer {dify_api_key}", "Content-Type": "application/json"}
    payload = {"inputs": {"review_text": comment}, "response_mode": "blocking", "user": "github-actions-scraper"}
    try:
        response = requests.post(dify_api_url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        result_text = response.json().get('outputs', {}).get('analysis_result')
        if not result_text: raise KeyError("'analysis_result' not found in Dify response.")
        return json.loads(result_text)
    except Exception as e:
        print(f"  └─ ❌ Dify 分析失敗: {e}")
        return None

def get_reviews_and_filter():
    print("\nSTEP 3: 開始抓取與篩選評論...")
    apps_to_scrape = [
        {'name': '三竹股市', 'platform': 'iOS', 'id': '352743563'},
        {'name': '三竹股市', 'platform': 'Android', 'id': 'com.mtk'},
        {'name': '富果 Fugle', 'platform': 'iOS', 'id': '1542310263'},
        {'name': '富果 Fugle', 'platform': 'Android', 'id': 'tw.fugle.flutter.app'},
    ]
    all_new_reviews = []
    for app in apps_to_scrape:
        print(f"  ▶️  正在處理: {app['name']} ({app['platform']})")
        try:
            if app['platform'] == 'iOS':
                scraper = AppStore(country='tw', app_id=app['id']); scraper.review(how_many=100)
                reviews_list = scraper.reviews
            else:
                reviews_list, _ = reviews(app['id'], lang='zh-TW', country='tw', sort=Sort.NEWEST, count=100)
            for review in reviews_list:
                all_new_reviews.append({
                    'app_name': app['name'], 'platform': app['platform'],
                    'comment': str(review.get('review') or review.get('content', '')),
                    'rating': int(review.get('rating') or review.get('score', 0)),
                    'date': (review.get('date') or review.get('at')).strftime('%Y-%m-%d %H:%M:%S')
                })
        except Exception as e: print(f"    └─ ⚠️ 抓取失敗: {e}")
    if not all_new_reviews: return []
    df = pd.DataFrame(all_new_reviews); df.drop_duplicates(subset=['comment'], inplace=True, keep='first')
    DIFY_WEEKLY_LIMIT = 40
    low_rating_reviews = df[df['rating'] <= 3].sort_values(by='date', ascending=False)
    high_rating_reviews = df[df['rating'] > 3].sort_values(by='date', ascending=False)
    if len(low_rating_reviews) >= DIFY_WEEKLY_LIMIT: reviews_to_analyze = low_rating_reviews.head(DIFY_WEEKLY_LIMIT)
    else:
        needed = DIFY_WEEKLY_LIMIT - len(low_rating_reviews)
        reviews_to_analyze = pd.concat([low_rating_reviews, high_rating_reviews.head(needed)])
    print(f"✅ 找到 {len(df)} 筆不重複評論，篩選後將分析 {len(reviews_to_analyze)} 筆。")
    return reviews_to_analyze.to_dict('records')

if __name__ == "__main__":
    reviews_to_process = get_reviews_and_filter()
    final_results_to_sheet = []
    if reviews_to_process:
        print("\nSTEP 4: 正在發送評論給 Dify 進行分析...")
        for i, review in enumerate(reviews_to_process):
            print(f"  - 分析中 {i+1}/{len(reviews_to_process)} ({review['app_name']}): \"{review['comment'][:40].replace('\n', ' ')}...\"")
            ai_result = analyze_with_dify(review['comment'])
            if ai_result:
                print(f"    └─ 🤖 AI 結果: {ai_result}")
                final_results_to_sheet.append({
                    'App_名稱': review['app_name'], '平台': review['platform'], '評論日期': review['date'],
                    '原始星等': review['rating'], '評論內容': review['comment'],
                    'AI情緒分數': ai_result.get('emotion_score'), 'AI分類': ai_result.get('category'),
                    'AI總結': ai_result.get('summary'), '處理時間': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                })
            else: print("    └─ ⚠️ 分析失敗，跳過。")
            time.sleep(1)
    if final_results_to_sheet:
        print(f"\nSTEP 5: 正在將 {len(final_results_to_sheet)} 筆結果寫入 Google Sheets...")
        try:
            headers = list(final_results_to_sheet[0].keys())
            worksheet.update('A1', [headers]) # 简化：每次都覆盖表头
            values_to_append = [list(row.values()) for row in final_results_to_sheet]
            worksheet.append_rows(values_to_append, value_input_option='USER_ENTERED')
            print("✅ 成功寫入 Google Sheets！")
        except Exception as e: print(f"❌ 寫入 Google Sheets 時發生錯誤: {e}")
    print("\n🎉 工作流程執行完畢！")
