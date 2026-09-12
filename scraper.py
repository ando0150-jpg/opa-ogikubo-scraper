import os
import re
import json
import requests
from playwright.sync_api import sync_playwright

GAS_URL = os.environ.get("GAS_WEBHOOK_URL")
TARGET_URL = "https://p-town.dmm.com/shops/tokyo/411"

def fetch_data():
    items = []
    
    with sync_playwright() as p:
        # ヘッドレスブラウザを一般的なChromeとして起動
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="ja-JP",
            timezone_id="Asia/Tokyo"
        )
        page = context.new_page()
        
        print(f"ターゲットURLへアクセス中: {TARGET_URL}")
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)  # レンダリング待機
        
        # ブラウザ内でDOMをパース（ブックマークレットと同様の安全なロジック）
        extracted = page.evaluate('''() => {
            const result = [];
            let currentRate = "[4] パチ";
            
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
            let node;
            
            while ((node = walker.nextNode())) {
                const txt = (node.innerText || "").trim();
                
                // レート見出しの検出
                if (node.matches && node.matches('h2, h3, h4, [class*="title"], [class*="heading"]')) {
                    if (/\[4\]|パチンコ\\s*[（(]4|4円/i.test(txt)) {
                        currentRate = "[4] パチ";
                    } else if (/\[1\]|パチンコ\\s*[（(]1|1円/i.test(txt)) {
                        currentRate = "[1] パチ";
                    } else if (/スロ|46枚|20円|1000円/i.test(txt)) {
                        currentRate = "[20] スロ";
                    }
                }
                
                // 機種名と台数の検出
                if (node.tagName === 'A' && node.href && node.href.includes('/machines/')) {
                    const parent = node.closest('li') || node.closest('tr') || node.parentElement;
                    if (parent) {
                        const m = parent.innerText.match(/(\\d+)\\s*台/);
                        const rawName = node.innerText.replace(/^NEW\\s*/i, '').replace(/\\s*NEW$/i, '').trim();
                        if (m && rawName) {
                            const count = parseInt(m[1], 10);
                            if (count > 0) {
                                result.push({
                                    rate: currentRate,
                                    name: rawName,
                                    count: count
                                });
                            }
                        }
                    }
                }
            }
            return result;
        }''')
        
        items = extracted
        browser.close()
        
    return items

def main():
    if not GAS_URL:
        raise ValueError("環境変数 GAS_WEBHOOK_URL が設定されていません。")
        
    print("データ取得を開始します...")
    items = fetch_data()
    
    total_units = sum(item["count"] for item in items)
    print(f"取得成功: {len(items)} 機種 / 合計 {total_units} 台")
    
    if len(items) == 0:
        raise Exception("データが0件のため送信を中断しました。")

    # GASへPOST送信
    print("GASへデータを送信中...")
    payload = json.dumps(items, ensure_ascii=False)
    
    # フォーム形式（postData）で送信してGAS側の処理と完全同期
    resp = requests.post(
        GAS_URL,
        data={"postData": payload},
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        timeout=60
    )
    
    print(f"GASレスポンス (HTTP {resp.status_code})")
    if resp.status_code == 200:
        print("✓ スプレッドシートの更新が完了しました！")
    else:
        print("✕ 送信失敗:", resp.text)
        raise Exception(f"GAS送信エラー: {resp.status_code}")

if __name__ == "__main__":
    main()
