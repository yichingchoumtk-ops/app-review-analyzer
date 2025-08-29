import os
import json
import requests
import pandas as pd
import gspread
from datetime import datetime
import time
from google_play_scraper import Sort, reviews
import re # 引入正則表達式函式庫

# ===================================================================
# V6.0 生产版
# 1. 移除所有 iOS 爬虫代码，专注于 Android
# 2. 新增“投资先生” App
# 3. 彻底重构 Dify 调用逻辑，增加重试与贪婪 JSON 解析，确保稳定性
# ===================================================================

print("--- main.py 脚本开始执行 (V6.0 生产版) ---")

# 1. 读取凭证
print("\nSTEP 1: 正在从环境变数读取凭证...")
try:
    dify_api_key = os.environ['DIFY_API_KEY'].strip()
    dify_api_url = os.environ['DIFY_API_URL'].strip()
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

# 3. 定义 Dify AI 分析功能 (V6.0 核心重构)
def analyze_with_dify(comment, max_retries=2):
    """
    调用 Dify Workflow API，内置重试与贪婪 JSON 解析逻辑。
    """
    headers = {"Authorization": f"Bearer {dify_api_key}", "Content-Type": "application/json"}
    payload = {"inputs": {"review_text": comment}, "response_mode": "blocking", "user": "github-actions-scraper"}
    
    for attempt in range(max_retries):
        try:
            response = requests.post(dify_api_url, headers=headers, json=payload, timeout=90)
            response.raise_for_status()
            raw_text = response.text

            # 贪婪的 JSON 提取器：寻找第一个 '{' 和最后一个 '}'
            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if match:
                json_str = match.group(0)
                # 尝试解析提取出的 JSON 字串
                result = json.loads(json_str)
                # 假设 Dify 工作流的输出包裹在 'outputs.analysis_result' 中
                # 如果直接是结果 JSON，则直接返回
                if 'outputs' in result and 'analysis_result' in result['outputs']:
                     # 如果 analysis_result 本身也是一个 JSON 字符串，需要再次解析
                    if isinstance(result['outputs']['analysis_result'], str):
                        return json.loads(result['outputs']['analysis_result'])
                    else:
                        return result['outputs']['analysis_result']
                else: # 兼容 Dify 直接返回分析结果的 JSON
                    return result

            else: # 如果连 JSON 的影子都找不到
                print(f"  └─ ⚠️ Dify 回应中未找到有效的 JSON 结构。回应: {raw_text[:100]}...")
                raise ValueError("No JSON object found in response")

        except Exception as e:
            print(f"  └─ ❌ Dify 分析尝试 {attempt + 1}/{max_retries} 失败: {e}")
            if attempt < max_retries - 1:
                print("     └─ 3秒后重试...")
                time.sleep(3)
            else:
                return None # 所有重试均失败
    return None

# 4. 定义爬虫与筛选功能
def get_reviews_and_filter():
    print("\nSTEP 3: 开始抓取与筛选 Android 评论...")
    apps_to_scrape = [
        {'name': '三竹股市', 'id': 'com.mtk'},
        {'name': '富果 Fugle', 'id': 'tw.fugle.flutter.app'},
        {'name': 'XQ 全球贏家', 'id': 'djapp.app.xqm'},
        {'name': '投資先生', 'id': 'com.yuanta.android.nexus'}, # 新增 App
    ]
    all_new_reviews = []
    for app in apps_to_scrape:
        print(f"  ▶️  正在处理: {app['name']}")
        try:
            reviews_list, _ = reviews(app['id'], lang='zh-TW', country='tw', sort=Sort.NEWEST, count=100)
            print(f"    └─ 成功抓取 {len(reviews_list)} 笔原始评论。")
            for review in reviews_list:
                all_new_reviews.append({
                    'app_name': app['name'], 'platform': 'Android',
                    'comment': str(review.get('content', '')),
                    'rating': int(review.get('score', 0)),
                    'date': review.get('at').strftime('%Y-%m-%d %H:%M:%S')
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
            time.sleep(2)
            
    if final_results_to_sheet:
        print(f"\nSTEP 5: 正在将 {len(final_results_to_sheet)} 笔结果写入 Google Sheets...")
        try:
            headers = list(final_results_to_sheet[0].keys())
            worksheet.clear() # 每次都清空重写
            worksheet.update('A1', [headers])
            values_to_append = [list(row.values()) for row in final_results_to_sheet]
            worksheet.append_rows(values_to_append, value_input_option='USER_ENTERED')
            print("✅ 成功写入 Google Sheets！")
        except Exception as e: print(f"❌ 写入 Google Sheets 时发生错误: {e}")
        
    print("\n🎉 工作流程执行完毕！")
