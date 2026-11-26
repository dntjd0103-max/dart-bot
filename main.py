import os
import requests
from datetime import datetime

DART_API_KEY = os.environ.get("DART_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    requests.post(url, json=payload)

def parse_number(value):
    """'2,000' 같은 문자열을 정수로 바꿔줌. 빈 값이면 0."""
    if not value or value == '-':
        return 0
    try:
        return int(str(value).replace(",", ""))
    except ValueError:
        return 0

def get_executive_detail(corp_code, rcept_no):
    """임원·주요주주 소유보고 상세(증감 수량, 직위, 최종 지분율) 조회"""
    url = f"https://opendart.fss.or.kr/api/elestock.json?crtfc_key={DART_API_KEY}&corp_code={corp_code}"
    res = requests.get(url).json()
    if res.get("status") != "000":
        return None
    for row in res.get("list", []):
        if row.get("rcept_no") == rcept_no:
            return row
    return None

def check_dart_buys():
    today = datetime.now().strftime("%Y%m%d")
    url = f"https://opendart.fss.or.kr/api/list.json?crtfc_key={DART_API_KEY}&bgn_de={today}&end_de={today}&page_count=100"
    response = requests.get(url).json()

    if response.get("status") != "000":
        print("조회 결과 없음 또는 에러:", response.get("message"))
        return

    reports = response.get("list", [])
    exec_lines = []
    major_lines = []

    for r in reports:
        report_name = r.get("report_nm", "")
        corp_name = r.get("corp_name", "")
        corp_code = r.get("corp_code", "")
        rcept_no = r.get("rcept_no", "")
        dart_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

        if "임원ㆍ주요주주특정증권" in report_name:
            detail = get_executive_detail(corp_code, rcept_no)
            if detail:
                change = parse_number(detail.get("sp_stock_lmp_irds_cnt"))
                position = detail.get("isu_exctv_ofcps") or "주요주주"
                reporter = detail.get("repror", "")
                rate = detail.get("sp_stock_lmp_rate", "")

                if change > 0:
                    change_text = f"매수 등 (+{change:,}주)"
                elif change < 0:
                    change_text = f"매도 등 ({change:,}주)"
                else:
                    change_text = "변동수량 확인 불가"

                exec_lines.append(
                    f"• *{corp_name}* | {reporter} ({position})\n"
                    f"  변동: {change_text}\n"
                    f"  최종 지분율: {rate}%\n"
                    f"  👉 [공시 상세보기]({dart_url})"
                )
            else:
                exec_lines.append(f"• *{corp_name}* | {report_name}\n  👉 [공시 보기]({dart_url})")

        elif "주식등의대량보유" in report_name:
            major_lines.append(f"• *{corp_name}* | {report_name}\n  👉 [공시 보기]({dart_url})")

    total = len(exec_lines) + len(major_lines)
    if total == 0:
        print("오늘 감지된 대주주/임원 지분 관련 공시가 없습니다.")
        return

    parts = [f"📊 *[오늘의 지분 변동 공시 요약 - 총 {total}건]*"]
    if exec_lines:
        parts.append(f"\n*▪ 임원·주요주주 지분 변동 ({len(exec_lines)}건)*\n\n" + "\n\n".join(exec_lines))
    if major_lines:
        parts.append(f"\n*▪ 5% 이상 대량보유 변동 ({len(major_lines)}건)*\n\n" + "\n\n".join(major_lines))

    send_telegram("\n".join(parts))
    print(f"총 {total}건의 공시를 요약해서 전송했습니다.")

check_dart_buys()
