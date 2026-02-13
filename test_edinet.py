"""EDINET API検証スクリプト"""
import requests
import json
from datetime import datetime, timedelta

API_KEY = "5808c5e540ad413ea772fe2a6e585ad9"
URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"

# テスト: 日産自動車(7201)の有価証券報告書を探す
TARGET_SEC_CODE = "72010"  # 5桁

print("=== EDINET API 検証 ===")
print(f"対象: 証券コード {TARGET_SEC_CODE}")
print()

# まず直近30日を検索して、どんなformCodeが返ってくるか確認
found_docs = []
today = datetime.now()

for days_ago in range(0, 90, 1):
    check_date = today - timedelta(days=days_ago)
    date_str = check_date.strftime("%Y-%m-%d")
    
    params = {"date": date_str, "type": 2, "Subscription-Key": API_KEY}
    try:
        resp = requests.get(URL, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"  {date_str}: HTTP {resp.status_code}")
            continue
        
        data = resp.json()
        results = data.get("results", [])
        
        for doc in results:
            sec_code = str(doc.get("secCode", ""))
            if sec_code == TARGET_SEC_CODE:
                form_code = doc.get("formCode", "")
                doc_desc = doc.get("docDescription", "")
                doc_id = doc.get("docID", "")
                submit_dt = doc.get("submitDateTime", "")
                print(f"  FOUND! date={date_str} formCode={form_code} docID={doc_id}")
                print(f"    desc={doc_desc}")
                print(f"    submit={submit_dt}")
                found_docs.append(doc)
    except Exception as e:
        print(f"  {date_str}: Error - {e}")

    # 見つかったら早期終了
    if len(found_docs) >= 3:
        break

    import time
    time.sleep(0.3)

print(f"\n=== 結果: {len(found_docs)} 件見つかりました ===")
if not found_docs:
    print("有価証券報告書が見つかりませんでした。")
    print("考えられる原因:")
    print("1. 検索日付範囲内に提出がない")
    print("2. secCodeの照合が合っていない")
    
    # secCode=None の書類も確認
    print("\n--- 直近1日の全secCode(非None)を確認 ---")
    params = {"date": today.strftime("%Y-%m-%d"), "type": 2, "Subscription-Key": API_KEY}
    resp = requests.get(URL, params=params, timeout=30)
    data = resp.json()
    codes = set()
    for doc in data.get("results", []):
        sc = doc.get("secCode")
        if sc:
            codes.add(str(sc))
    print(f"  secCode付きの書類数: {len([d for d in data.get('results',[]) if d.get('secCode')])}")
    print(f"  ユニーク secCode数: {len(codes)}")
    print(f"  例: {list(codes)[:10]}")
