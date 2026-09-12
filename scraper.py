import os
import sys
import re
import json
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

GAS_URL = os.environ.get("GAS_WEBHOOK_URL")
TARGET_URL = "https://p-town.dmm.com/shops/tokyo/411"

def parse_html_content(html_text):
    """HTMLからレートと機種・台数を抽出する共通パーサー"""
    soup = BeautifulSoup(html_text, "html.parser")
    items = []
    current_rate = "[4] パチ"

    # レート見出しとリンクを順次探索
    elements = soup.find_all(["h2", "h3", "h4", "div", "li", "tr", "a"])
    for el in elements:
        txt = el.get_text().strip()
        
        # レート見出しの検出
        if re.search(r'\[4\]|パチンコ\s*[（(]4|4円', txt):
            current_rate = "[4] パチ"
        elif re.search(r'\[1\]|パチンコ\s*[（(]1|1円', txt):
            current_rate = "[1] パチ"
        elif re.search(r'スロ|46枚|20円|1000円', txt):
            current_rate = "[20] スロ"

        # 機種リンクと台数の検出
        if el.name == 'a' and el.get('href') and '/machines/' in el.get('href', ''):
            parent = el.find_parent(["li", "tr"]) or el.parent
            if parent:
                parent_text = parent.get_text()
                m = re.search(r'(\d+)\s*台', parent_text)
                name = re.sub(r'^(NEW|\s)+', '', el.get_text()).strip()
                name = re.sub(r'(NEW|\s)+$', '', name).strip()
                if m and name:
                    count = int(m.group(1))
                    if count > 0:
                        items.append({
                            "rate": current_rate,
                            "name": name,
                            "count": count
                        })
                        
    # 重複除去（同一レート・同一機種は合算）
    merged = {}
    for item in items:
        key = f"{item['rate']}___{item['name']}"
        if key in merged:
            merged[key]["count"] = max(merged[key]["count"], item["count"])
        else:
            merged[key] = item

    return list(merged.values())

def fetch_via_requests():
    """軽量なHTTPリクエストで取得を試みる"""
    print("[試行 1] requests によるHTML直接取得...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    try:
        resp = requests.get(TARGET_URL, headers=headers, timeout=20)
        print(f"  HTTPステータス: {resp.status_code}")
        if resp.status_code == 200 and "オーパ荻窪" in resp.text:
            items = parse_html_content(resp.text)
            if len(items) > 0:
                print(f"  requests で {len(items)} 機種の抽出に成功！")
                return items
    except Exception as e:
        print(f"  requests 失敗: {e}")
    return []

def fetch_via_playwright():
    """ステルス設定を施したPlaywrightで取得を試みる"""
    print("[試行 2] ステルスPlaywrightブラウザによる取得...")
    items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage"
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="ja-JP",
            timezone_id="Asia/Tokyo"
        )
        page = context.new_page()

        # navigator.webdriver を隠蔽
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        print(f"  ページアクセス中: {TARGET_URL}")
        try:
            resp = page.goto(TARGET_URL, wait_until="networkidle", timeout=35000)
            status = resp.status if resp else "unknown"
            title = page.title()
            print(f"  応答ステータス: {status}, タイトル: {title}")
        except Exception as e:
            print(f"  goto 警告: {e} (DOM解析を継続します)")

        page.wait_for_timeout(4000)
        html_content = page.content()
        
        # 画面に何が表示されているか診断
        body_snippet = page.evaluate("() => document.body.innerText.slice(0, 300)")
        print(f"  画面テキスト抜粋:\n---\n{body_snippet}\n---")

        items = parse_html_content(html_content)
        browser.close()

    return items

def main():
    print("=== データ収集開始 ===")
    if not GAS_URL:
        print("エラー: GAS_WEBHOOK_URL が設定されていません。")
        sys.exit(1)

    # 1. requests で試行、ダメなら Playwright で試行
    items = fetch_via_requests()
    if not items:
        items = fetch_via_playwright()

    total_units = sum(item["count"] for item in items)
    print(f"=== 取得結果: {len(items)} 機種 / 合計 {total_units} 台 ===")

    if len(items) == 0:
        print("エラー: 機種データが0件でした。上記ログの画面テキスト抜粋をご確認ください。")
        sys.exit(1)

    print("Google Apps Script へ送信中...")
    payload = json.dumps(items, ensure_ascii=False)
    resp = requests.post(
        GAS_URL,
        data={"postData": payload},
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        timeout=60
    )
    print(f"GAS応答: HTTP {resp.status_code}")
    if resp.status_code == 200:
        print("✓ スプレッドシートの更新が正常に完了しました！")
    else:
        print(f"✕ 送信エラー: {resp.text}")
        sys.exit(1)

if __name__ == "__main__":
    main()
