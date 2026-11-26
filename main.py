"""
DART 대주주(임원·주요주주) 매수 알림

하루치 '임원ㆍ주요주주특정증권등소유상황보고서' 공시를 모아
보유 주식 수가 늘어난 건만 골라 텔레그램으로 알려주고 data/alerts.csv에 기록한다.

신호 강도 판단 (본인 가설):
  - 이례적 매수: 같은 사람이 지난 3년 동안 매년 같은 달에 매수해 왔다면 '습관적(루틴)' 매수로 보고,
                 그렇지 않으면 '이례적' 매수로 본다.
  - 복수 내부자 매수: 최근 30일 안에 같은 회사에서 2명 이상의 내부자가 매수했다면 더 강한 신호로 본다.

사용법:
  python main.py                 # 오늘 공시
  python main.py --date 20261120 # 특정 날짜 공시 (테스트용)
  python main.py --dry-run       # 텔레그램 전송 없이 결과만 출력
"""

import argparse
import csv
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

DART_API = "https://opendart.fss.or.kr/api"
INSIDER_TYPE = "D002"  # 임원ㆍ주요주주특정증권등소유상황보고서
MAJOR_TYPE = "D001"    # 주식등의대량보유상황보고서 (5% 보고)
KST = timezone(timedelta(hours=9))
LOG_PATH = Path(__file__).parent / "data" / "alerts.csv"
CLUSTER_DAYS = 30
ROUTINE_YEARS = 3


# ---------- 숫자/날짜 정리 ----------

def to_number(value):
    """'1,234' / '-1,234' / '-' / '' 같은 문자열을 숫자로 바꾼다. 못 바꾸면 0."""
    if value is None:
        return 0.0
    text = str(value).replace(",", "").strip()
    if text in ("", "-"):
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def to_date(value):
    text = str(value).replace("-", "").replace(".", "").strip()[:8]
    return datetime.strptime(text, "%Y%m%d").date()


# ---------- DART API ----------

def dart_get(endpoint, api_key, **params):
    params["crtfc_key"] = api_key
    for attempt in range(3):
        try:
            resp = requests.get(f"{DART_API}/{endpoint}.json", params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")
            if status == "000":
                return data
            if status == "013":  # 조회된 데이터 없음
                return {"list": [], "total_page": 0}
            raise RuntimeError(f"DART 오류 {status}: {data.get('message')}")
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2)
    return {"list": []}


def fetch_reports(api_key, date_str, detail_type=INSIDER_TYPE):
    """해당 날짜의 지분공시 목록 (상장사만, 정정공시 제외)."""
    reports, page = [], 1
    while True:
        data = dart_get(
            "list", api_key,
            bgn_de=date_str, end_de=date_str,
            pblntf_ty="D", pblntf_detail_ty=detail_type,
            page_no=page, page_count=100,
        )
        reports.extend(data.get("list", []))
        if page >= int(data.get("total_page") or 0):
            break
        page += 1

    result = []
    for r in reports:
        if r.get("corp_cls") not in ("Y", "K"):  # Y=코스피, K=코스닥
            continue
        if "정정" in r.get("report_nm", ""):
            continue
        result.append(r)
    return result


def fetch_insider_history(api_key, corp_code):
    """한 회사의 임원·주요주주 소유보고 전체 이력."""
    return dart_get("elestock", api_key, corp_code=corp_code).get("list", [])


def fetch_major_history(api_key, corp_code):
    """한 회사의 5% 대량보유 보고 전체 이력."""
    return dart_get("majorstock", api_key, corp_code=corp_code).get("list", [])


def analyze_major(report, history):
    """5% 대량보유 보고 중 보유 주식이 늘어난 건."""
    alerts = []
    for row in history:
        if row.get("rcept_no") != report["rcept_no"]:
            continue
        change = to_number(row.get("stkqy_irds"))
        if change <= 0:
            continue
        alerts.append({
            "type": "대량보유",
            "date": to_date(row["rcept_dt"]).isoformat(),
            "corp_name": report.get("corp_name", ""),
            "stock_code": report.get("stock_code", ""),
            "market": "코스피" if report.get("corp_cls") == "Y" else "코스닥",
            "reporter": row.get("repror", ""),
            "position": "5% 이상 주주",
            "change_shares": int(change),
            "total_shares": int(to_number(row.get("stkqy"))),
            "change_rate": to_number(row.get("stkrt_irds")),
            "total_rate": to_number(row.get("stkrt")),
            "routine": "",
            "cluster": "",
            "reason": (row.get("report_resn") or "").strip()[:60],
            "rcept_no": report["rcept_no"],
        })
    return alerts


# ---------- 신호 판단 ----------

def buys_only(history):
    return [h for h in history if to_number(h.get("sp_stock_lmp_irds_cnt")) > 0]


def is_routine(person, buy_date, history_buys):
    """지난 3년 동안 매년 같은 달에 매수했다면 루틴 매수."""
    months = {
        (to_date(h["rcept_dt"]).year, to_date(h["rcept_dt"]).month)
        for h in history_buys
        if h.get("repror") == person and to_date(h["rcept_dt"]) < buy_date
    }
    return all((buy_date.year - k, buy_date.month) in months for k in range(1, ROUTINE_YEARS + 1))


def cluster_size(buy_date, history_buys):
    """최근 30일 안에 매수한 서로 다른 내부자 수."""
    start = buy_date - timedelta(days=CLUSTER_DAYS)
    return len({
        h.get("repror")
        for h in history_buys
        if start <= to_date(h["rcept_dt"]) <= buy_date
    })


def analyze(report, history):
    """오늘 공시 중 매수 건을 찾아 신호 정보를 붙여 돌려준다."""
    rows = [h for h in history if h.get("rcept_no") == report["rcept_no"]]
    history_buys = buys_only(history)
    alerts = []
    for row in rows:
        change = to_number(row.get("sp_stock_lmp_irds_cnt"))
        if change <= 0:
            continue
        buy_date = to_date(row["rcept_dt"])
        person = row.get("repror", "")
        routine = is_routine(person, buy_date, history_buys)
        cluster = cluster_size(buy_date, history_buys)
        alerts.append({
            "type": "임원·주요주주",
            "reason": "",
            "date": buy_date.isoformat(),
            "corp_name": report.get("corp_name", ""),
            "stock_code": report.get("stock_code", ""),
            "market": "코스피" if report.get("corp_cls") == "Y" else "코스닥",
            "reporter": person,
            "position": row.get("isu_exctv_ofcps") or row.get("isu_main_shrholdr") or "",
            "change_shares": int(change),
            "total_shares": int(to_number(row.get("sp_stock_lmp_cnt"))),
            "change_rate": to_number(row.get("sp_stock_lmp_irds_rate")),
            "total_rate": to_number(row.get("sp_stock_lmp_rate")),
            "routine": routine,
            "cluster": cluster,
            "rcept_no": report["rcept_no"],
        })
    return alerts


def signal_label(alert):
    tags = []
    if alert["cluster"] >= 2:
        tags.append(f"🔥 복수 내부자 매수({alert['cluster']}명/30일)")
    tags.append("🔁 루틴 매수" if alert["routine"] else "⚡ 이례적 매수")
    return " · ".join(tags)


def signal_score(alert):
    """정렬용 점수: 복수 매수 > 이례적 > 지분율 변화 크기."""
    return (alert["cluster"] >= 2, not alert["routine"], alert["change_rate"])


# ---------- 출력 ----------

def format_message(alerts, major_alerts, date_str):
    title = f"📈 <b>내부자 매수 알림</b> ({date_str[:4]}.{date_str[4:6]}.{date_str[6:]})\n"
    if not alerts and not major_alerts:
        return title + "\n오늘은 보유 주식이 늘어난 임원·주요주주 / 5% 대량보유 공시가 없습니다."
    lines = [title, f"임원·주요주주 {len(alerts)}건 · 5% 대량보유 {len(major_alerts)}건\n"]
    if alerts:
        lines.append("━━ <b>임원·주요주주 매수</b> ━━\n")
    for a in alerts:
        url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={a['rcept_no']}"
        lines.append(
            f"<b>{a['corp_name']}</b> ({a['stock_code']}, {a['market']})\n"
            f"{a['reporter']} {a['position']}\n"
            f"+{a['change_shares']:,}주 (지분 {a['change_rate']:+.2f}%p → {a['total_rate']:.2f}%)\n"
            f"{signal_label(a)}\n"
            f"<a href=\"{url}\">공시 원문</a>\n"
        )
    if major_alerts:
        lines.append("━━ <b>5% 대량보유 증가</b> ━━\n")
    for a in major_alerts:
        url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={a['rcept_no']}"
        reason = f"사유: {a['reason']}\n" if a["reason"] else ""
        lines.append(
            f"<b>{a['corp_name']}</b> ({a['stock_code']}, {a['market']})\n"
            f"{a['reporter']}\n"
            f"+{a['change_shares']:,}주 (지분 {a['change_rate']:+.2f}%p → {a['total_rate']:.2f}%)\n"
            f"{reason}"
            f"<a href=\"{url}\">공시 원문</a>\n"
        )
    return "\n".join(lines)


def send_telegram(text, token, chat_id):
    # 텔레그램 메시지는 4096자 제한이 있어 나눠 보낸다.
    chunks, current = [], ""
    for block in text.split("\n\n"):
        if len(current) + len(block) > 3800:
            chunks.append(current)
            current = ""
        current += block + "\n\n"
    chunks.append(current)
    for chunk in chunks:
        if not chunk.strip():
            continue
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                  "disable_web_page_preview": "true"},
            timeout=30,
        )
        resp.raise_for_status()


def save_log(alerts):
    LOG_PATH.parent.mkdir(exist_ok=True)
    fields = ["type", "date", "corp_name", "stock_code", "market", "reporter", "position",
              "change_shares", "total_shares", "change_rate", "total_rate",
              "routine", "cluster", "reason", "rcept_no"]
    existing = set()
    if LOG_PATH.exists():
        with LOG_PATH.open(encoding="utf-8-sig") as f:
            existing = {(r["rcept_no"], r["reporter"]) for r in csv.DictReader(f)}
    new_file = not LOG_PATH.exists()
    with LOG_PATH.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if new_file:
            writer.writeheader()
        for a in alerts:
            if (a["rcept_no"], a["reporter"]) not in existing:
                writer.writerow(a)


# ---------- 실행 ----------

def run(date_str, api_key, dry_run=False):
    reports = fetch_reports(api_key, date_str, INSIDER_TYPE)
    major_reports = fetch_reports(api_key, date_str, MAJOR_TYPE)
    print(f"{date_str} 임원·주요주주 보고 {len(reports)}건, 5% 대량보유 보고 {len(major_reports)}건")

    histories, alerts = {}, []
    for r in reports:
        code = r["corp_code"]
        if code not in histories:
            histories[code] = fetch_insider_history(api_key, code)
            time.sleep(0.2)  # API 호출 간격
        alerts.extend(analyze(r, histories[code]))

    major_histories, major_alerts = {}, []
    for r in major_reports:
        code = r["corp_code"]
        if code not in major_histories:
            major_histories[code] = fetch_major_history(api_key, code)
            time.sleep(0.2)
        major_alerts.extend(analyze_major(r, major_histories[code]))

    alerts.sort(key=signal_score, reverse=True)
    major_alerts.sort(key=lambda a: a["change_rate"], reverse=True)
    message = format_message(alerts, major_alerts, date_str)
    print(message)

    save_log(alerts + major_alerts)
    if not dry_run:
        # 기존 dart-bot Secrets 이름(TELEGRAM_TOKEN, CHAT_ID)을 그대로 사용
        token = os.environ.get("TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
        if token and chat_id:
            send_telegram(message, token, chat_id)
        else:
            print("텔레그램 설정이 없어 전송하지 않았습니다.")
    return alerts, major_alerts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYYMMDD (기본: 오늘)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("DART_API_KEY")
    if not api_key:
        raise SystemExit("DART_API_KEY가 설정되지 않았습니다.")
    date_str = args.date or datetime.now(KST).strftime("%Y%m%d")
    run(date_str, api_key, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
