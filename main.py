import os
import json
import requests
import pandas as pd
import gspread
from datetime import datetime
from app_store_scraper import AppStore
from google_play_scraper import Sort, reviews
import time

# ===================================================================
# 1. 从 GitHub Secrets 读取安全凭证
# ===================================================================
print("STEP 1: Reading credentials from GitHub Secrets...")
try:
    dify_api_key = os.environ['DIFY_API_KEY']
    dify_api_url = os.environ['DIFY_API_URL']
    google_creds_json = os.environ['GOOGLE_SHEETS_CREDENTIALS']
    
    # 将 JSON 字符串转换为 gspread 需要的字典格式
    google_creds_dict = json.loads(google_creds_json)
    
    print("✅ Successfully read all credentials.")
except KeyError as e:
    print(f"❌ ERROR: A required secret is missing in GitHub Secrets: {e}")
    exit(1) # 严重错误，直接退出程序

# ===================================================================
# 2. 设定 Google Sheets 连接
# ===================================================================
print("\nSTEP 2: Connecting to Google Sheets...")
try:
    gc = gspread.service_account_from_dict(google_creds_dict)
    
    # !!! 唯一需要你确认和修改的地方 !!!
    # 请确保这里的名称与你的 Google Sheet 档案名称完全一致
    spreadsheet_name = "App 評論自動化洞察系統"
    spreadsheet = gc.open(spreadsheet_name) 
    worksheet_name = "評論資料庫 (Reviews_DB)"
    worksheet = spreadsheet.worksheet(worksheet_name)
    
    print(f"✅ Successfully connected to Google Sheet: '{spreadsheet_name}' -> Worksheet: '{worksheet_name}'")
except Exception as e:
    print(f"❌ ERROR: Failed to connect to Google Sheets: {e}")
    exit(1)

# ===================================================================
# 3. 定义 Dify AI 分析功能
# ===================================================================
def analyze_with_dify(comment):
    """
    调用 Dify Workflow API 对单条评论进行分析。
    """
    headers = {
        "Authorization": f"Bearer {dify_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": {
            "review_text": comment # 这个 key 必须与 Dify "开始"节点定义的变量名一致
        },
        "response_mode": "blocking",
        "user": "github-actions-scraper" 
    }
    
    try:
        response = requests.post(dify_api_url, headers=headers, json=payload, timeout=60) # 增加60秒超时
        response.raise_for_status() # 如果请求失败 (如 4xx or 5xx)，会抛出异常
        
        response_json = response.json()
        
        # Dify 工作流的输出通常在 'outputs' 键中
        # 假设我们在 Dify 的结束节点将输出命名为 'analysis_result'
        result_text = response_json.get('outputs', {}).get('analysis_result')
        
        if not result_text:
            raise KeyError("'analysis_result' not found in Dify response outputs.")

        return json.loads(result_text)
        
    except requests.exceptions.RequestException as e:
        print(f"  └─ ❌ Dify API call failed: {e}")
    except (KeyError, json.JSONDecodeError) as e:
        print(f"  └─ ❌ Failed to parse Dify response: {e}, Response content: {response.text}")
        
    return None # 如果任何步骤失败，则返回 None

# ===================================================================
# 4. 定义爬虫功能与智能筛选
# ===================================================================
def get_reviews_and_filter():
    """
    抓取所有 App 的最新评论，并进行智能筛选。
    """
    # 在未来，我们可以从 Google Sheet 的 '40个app' 分页动态读取这个清单
    # 为了简化 Phase 1，我们先写死几个 App 进行测试
    apps_to_scrape = [
        {'name': '三竹股市', 'platform': 'iOS', 'id': '352743563'},
        {'name': '三竹股市', 'platform': 'Android', 'id': 'com.mtk'},
        {'name': '富果 Fugle', 'platform': 'iOS', 'id': '1542310263'},
        {'name': '富果 Fugle', 'platform': 'Android', 'id': 'tw.fugle.flutter.app'},
        {'name': 'XQ 全球贏家', 'platform': 'iOS', 'id': '642738082'},
        {'name': 'XQ 全球贏家', 'platform': 'Android', 'id': 'djapp.app.xqm'},
    ]
    
    all_new_reviews = []
    
    for app in apps_to_scrape:
        print(f"\n▶️  Fetching reviews for: {app['name']} ({app['platform']})")
        try:
            if app['platform'] == 'iOS':
                scraper = AppStore(country='tw', app_id=app['id'])
                scraper.review(how_many=100) # 抓取最新的 100 笔评论
                reviews_list = scraper.reviews
            else: # Android
                reviews_list, _ = reviews(app['id'], lang='zh-TW', country='tw', sort=Sort.NEWEST, count=100)

            for review in reviews_list:
                 all_new_reviews.append({
                    'app_name': app['name'],
                    'platform': app['platform'],
                    'comment': str(review.get('review') or review.get('content', '')), # 兼容 iOS/Android 并确保是字符串
                    'rating': int(review.get('rating') or review.get('score', 0)),
                    'date': (review.get('date') or review.get('at')).strftime('%Y-%m-%d %H:%M:%S')
                 })
        except Exception as e:
            print(f"  └─ ⚠️ Could not fetch reviews for this app: {e}")
             
    if not all_new_reviews:
        print("\nNo new reviews found across all apps.")
        return []

    # --- 智能筛选 ---
    # 为了不超過 Dify 每月 200 次的限制，我们每周的目标是分析约 40-50 条评论
    DIFY_WEEKLY_LIMIT = 40
    
    df = pd.DataFrame(all_new_reviews)
    df.drop_duplicates(subset=['comment'], inplace=True, keep='first') # 去重
    
    # 策略：优先选择 3 星及以下的，如果还不够，再用最新的 4-5 星评论补足
    low_rating_reviews = df[df['rating'] <= 3].sort_values(by='date', ascending=False)
    high_rating_reviews = df[df['rating'] > 3].sort_values(by='date', ascending=False)
    
    if len(low_rating_reviews) >= DIFY_WEEKLY_LIMIT:
        reviews_to_analyze = low_rating_reviews.head(DIFY_WEEKLY_LIMIT)
    else:
        needed = DIFY_WEEKLY_LIMIT - len(low_rating_reviews)
        reviews_to_analyze = pd.concat([low_rating_reviews, high_rating_reviews.head(needed)])
        
    print(f"\n✅ Found {len(df)} unique new reviews. After smart filtering, {len(reviews_to_analyze)} will be sent for AI analysis.")
    return reviews_to_analyze.to_dict('records')

# ===================================================================
# 5. 主执行流程
# ===================================================================
if __name__ == "__main__":
    
    print("\nSTEP 3: Fetching and filtering reviews...")
    reviews_to_process = get_reviews_and_filter()
    final_results_to_sheet = []
    
    if reviews_to_process:
        print("\nSTEP 4: Sending reviews to Dify for analysis...")
        for i, review in enumerate(reviews_to_process):
            print(f"  - Analyzing review {i+1}/{len(reviews_to_process)} ({review['app_name']}): \"{review['comment'][:40].replace('\n', ' ')}...\"")
            
            # 呼叫 AI 进行分析
            ai_result = analyze_with_dify(review['comment'])
            
            if ai_result:
                print(f"  └─ 🤖 AI Result: {ai_result}")
                # 合併原始评论和 AI 分析结果
                final_row = {
                    'App_名稱': review['app_name'],
                    '平台': review['platform'],
                    '評論日期': review['date'],
                    '原始星等': review['rating'],
                    '評論內容': review['comment'],
                    'AI情緒分數': ai_result.get('emotion_score'),
                    'AI分類': ai_result.get('category'),
                    'AI總結': ai_result.get('summary'),
                    '處理時間': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
                final_results_to_sheet.append(final_row)
            else:
                print("  └─ ⚠️ AI analysis failed for this review, skipping.")
            
            time.sleep(1) # 礼貌性地等待1秒，避免请求过于频繁

    if final_results_to_sheet:
        print(f"\nSTEP 5: Writing {len(final_results_to_sheet)} results to Google Sheets...")
        
        try:
            # 检查表头是否已存在，如果不存在或不匹配，则先写入表头
            headers = list(final_results_to_sheet[0].keys())
            try:
                sheet_headers = worksheet.row_values(1)
            except gspread.exceptions.APIError: # 处理工作表为空的情况
                sheet_headers = []

            if sheet_headers != headers:
                print("  - Writing headers to the sheet...")
                worksheet.update('A1', [headers])
            
            # 将字典列表转换为 gspread 需要的格式 (列表的列表)
            values_to_append = [list(row.values()) for row in final_results_to_sheet]
            worksheet.append_rows(values_to_append, value_input_option='USER_ENTERED')
            
            print("✅ Successfully wrote results to Google Sheets!")
        except Exception as e:
            print(f"❌ ERROR: An error occurred while writing to Google Sheets: {e}")
            
    print("\n🎉 Workflow finished!")
