import os
import json
import requests
import pandas as pd
import gspread
from datetime import datetime
import time
from app_store_scraper import AppStore
from google_play_scraper import Sort, reviews

# ===================================================================
# V5.2 最终修复版
# 修正: 修复了第 130 行的 f-string 语法错误
# ===================================================================

print("--- main.py 脚本开始执行 ---")

# ... (前面 1-3 部分的代码完全不变，这里为了简洁先省略) ...
# 为了避免混淆，还是提供完整版

# 1. 读取凭证
print("\nSTEP 1: 正在从环境变数读取凭证...")
try:
    dify_api_key = os.environ['DIFY_API_KEY']
    dify_api_url = os.environ['DIFY_API_URL']
    google_creds_json = os.environ['GOOGLE_SHEETS_CREDENTIALS']
    google_creds_dict = json.loads(google_creds_json)
    print("✅ 成功读取所有凭证。")
except Exception as e:
    print(f"❌ 致命错误：读取或解析凭证失败: {e}")
    exit(1)

# 2. 连接 Google Sheets
print("\nSTEP 2: 正在连接到 Google Sheets...")
try:
    gc = gspread.service_account_from_dict(google_creds_dict)
    spreadsheet_name = "App 評論自動化洞察系統"
    spreadsheet = gc.open(spreadsheet_name)
    worksheet_name = "評論資料庫 (Reviews_DB)"
    worksheet = spreadsheet.worksheet(worksheet_name)
    print(f"✅ 成功连接到 Google Sheet: '{spreadsheet_name}' -> Worksheet: '{worksheet_name}'")
except Exception as e:
    print(f"❌ 连接 Google Sheets 时发生致命错误: {e}")
    exit(1)

# 3. 定义 Dify AI 分析功能
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
        print(f"  └─ ❌ Dify 分析失败: {e}")
        return None

# 4. 定义爬虫与筛选功能
def get_reviews_and_filter():
    print("\nSTEP 3: 开始抓取与筛选评论...")
    apps_to_scrape = [
        {'name': '三竹股市', 'platform': 'iOS', 'id': '352743563'},
        {'name': '三竹股市', 'platform': 'Android', 'id': 'com.mtk'},
        {'name': '富果 Fugle', 'platform': 'iOS', 'id': '1542310263'},
        {'name': '富果 Fugle', 'platform': 'Android', 'id': 'tw.fugle.flutter.app'},
    ]
    all_new_reviews = []
    for app in apps_to_scrape:
        print(f"  ▶️  正在处理: {app['name']} ({app['platform']})")
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
        except Exception as e: print(f"    └─ ⚠️ 抓取失败: {e}")
    if not all_new_reviews: return []
    df = pd.DataFrame(all_new_reviews); df.drop_duplicates(subset=['comment'], inplace=True, keep='first')
    DIFY_WEEKLY_LIMIT = 40
    low_rating_reviews = df[df['rating'] <= 3].sort_values(by='date', ascending=False)
    high_rating_reviews = df[df['rating'] > 3].sort_values(by='date', ascending=False)
    if len(low_rating_reviews) >= DIFY_WEEKLY_LIMIT: reviews_to_analyze = low_rating_reviews.head(DIFY_WEEKLY_LIMIT)
    else:
        needed = DIFY_WEEKLY_LIMIT - len(low_rating_reviews)
        reviews_to_analyze = pd.concat([low_rating_reviews, high_rating_reviews.head(needed)])
    print(f"✅ 找到 {len(df)} 笔不重複评论，筛选後将分析 {len(reviews_to_analyze)} 笔。")
    return reviews_to_analyze.to_dict('records')

# 5. 主执行流程
if __name__ == "__main__":
    reviews_to_process = get_reviews_and_filter()
    final_results_to_sheet = []
    if reviews_to_process:
        print("\nSTEP 4: 正在发送评论给 Dify 进行分析...")
        for i, review in enumerate(reviews_to_process):
            # V5.2 修正处：将 .replace('\n', ' ') 移到 f-string 外部，避免语法错误
            comment_preview = review['comment'][:40].replace('\n', ' ')
            print(f"  - 分析中 {i+1}/{len(reviews_to_process)} ({review['app_name']}): \"{comment_preview}...\"")
            
            ai_result = analyze_with_dify(review['comment'])
            if ai_result:
                print(f"    └─ 🤖 AI 结果: {ai_result}")
                final_results_to_sheet.append({
                    'App_名稱': review['app_name'], '平台': review['platform'], '評論日期': review['date'],
                    '原始星等': review['rating'], '評論內容': review['comment'],
                    'AI情緒分數': ai_result.get('emotion_score'), 'AI分類': ai_result.get('category'),
                    'AI總結': ai_result.get('summary'), '處理時間': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                })
            else: print("    └─ ⚠️ 分析失败，跳过。")
            time.sleep(1)
            
    if final_results_to_sheet:
        print(f"\nSTEP 5: 正在将 {len(final_results_to_sheet)} 笔结果写入 Google Sheets...")
        try:
            headers = list(final_results_to_sheet[0].keys())
            try:
                sheet_headers = worksheet.row_values(1)
            except gspread.exceptions.APIError: sheet_headers = []
            
            # 简化逻辑：每次都清空并重写，以避免 POC 阶段数据重复
            print("  - 正在清空工作表并写入新数据...")
            worksheet.clear()
            worksheet.update('A1', [headers])
            values_to_append = [list(row.values()) for row in final_results_to_sheet]
            worksheet.append_rows(values_to_append, value_input_option='USER_ENTERED')
            print("✅ 成功写入 Google Sheets！")
        except Exception as e: print(f"❌ 写入 Google Sheets 时发生错误: {e}")
        
    print("\n🎉 工作流程执行完毕！")
