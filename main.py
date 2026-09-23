import os
import requests
from datetime import datetime

# GitHub에 안전하게 저장된 비밀키를 불러옵니다.
DART_API_KEY = os.environ.get("DART_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

def check_dart_buys():
    today = datetime.now().strftime("%Y%m%d")
    url = f"https://opendart.fss.or.kr/api/list.json?crtfc_key={DART_API_KEY}&bgn_de={today}&end_de={today}&page_count=100"
    
    response = requests.get(url).json()
    if response.get("status") != "000":
        print("조회 결과 없음 또는 에러:", response.get("message"))
        return
    
    reports = response.get("list", [])
    found_count = 0
    for r in reports:
        report_name = r.get("report_nm", "")
        corp_name = r.get("corp_name", "")
        rcept_no = r.get("rcept_no", "")
        
        if "임원ㆍ주요주주특정증권" in report_name or "주식등의대량보유" in report_name:
            found_count += 1
            dart_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
            msg = (
                f"🚨 *[지분 변동 공시 포착]*\n\n"
                f"• **회사명**: {corp_name}\n"
                f"• **보고서명**: {report_name}\n"
                f"• **링크**: [공시 바로가기]({dart_url})"
            )
            send_telegram(msg)
            
    if found_count == 0:
        print("오늘 감지된 대주주/임원 지분 관련 공시가 없습니다.")

check_dart_buys()
