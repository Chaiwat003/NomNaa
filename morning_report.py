"""Generate a cute morning sales report for yesterday and send to Telegram.

Requirements:
- expects `sheets_client.get_sheet()` to return a gspread sheet
- env vars: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GOOGLE_SHEETS_ID`, and service account vars

Usage:
  python morning_report.py [--dry-run]

The script reads all rows from the sheet, filters rows from yesterday,
aggregates totals and best-selling menu, and sends a Telegram message via `requests`.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
import argparse

from dotenv import load_dotenv
import requests

from sheets_client import get_sheet


def load_env() -> None:
    load_dotenv()


def parse_iso(dt_str: str) -> datetime | None:
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        try:
            # fallback: try to strip timezone Z
            if dt_str.endswith("Z"):
                return datetime.fromisoformat(dt_str[:-1])
        except Exception:
            return None
    return None


def build_report(rows: list[list[str]]) -> dict:
    """Rows include header row. Returns a dict with totals and top menus."""
    if not rows:
        return {}

    header = rows[0]
    data = rows[1:]

    yesterday = (datetime.now() - timedelta(days=1)).date()

    total_sales = 0.0
    total_items = 0
    menu_qty: dict[str, int] = defaultdict(int)
    menu_revenue: dict[str, float] = defaultdict(float)

    for r in data:
        if len(r) < 5:
            continue
        date_str, name, qty_str, price_str, total_str = r[0:5]
        dt = parse_iso(date_str)
        if not dt:
            continue
        if dt.date() != yesterday:
            continue

        try:
            qty = int(qty_str)
        except Exception:
            qty = 0
        try:
            total = float(total_str)
        except Exception:
            # fallback compute
            try:
                price = float(price_str)
                total = qty * price
            except Exception:
                total = 0.0

        total_sales += total
        total_items += 1
        menu_qty[name] += qty
        menu_revenue[name] += total

    best_by_qty = None
    best_by_revenue = None
    if menu_qty:
        best_by_qty = max(menu_qty.items(), key=lambda x: x[1])
    if menu_revenue:
        best_by_revenue = max(menu_revenue.items(), key=lambda x: x[1])

    return {
        "date": yesterday.isoformat(),
        "total_sales": total_sales,
        "total_items": total_items,
        "best_by_qty": best_by_qty,
        "best_by_revenue": best_by_revenue,
        "menu_qty": dict(menu_qty),
        "menu_revenue": dict(menu_revenue),
    }


def render_message(rep: dict) -> str:
    if not rep:
        return "ไม่มีข้อมูลของเมื่อวานเลยค่า 😭\nพักผ่อนเยอะๆ นะคะ แล้วค่อยเริ่มใหม่พรุ่งนี้นะ 💪💕"

    lines = []
    lines.append(f"สรุปยอดเมื่อวานน้าาา 🥳 \nวันที่: {rep['date']}")
    lines.append(f"ยอดรวม: ฿{rep['total_sales']} 💸")
    lines.append(f"จำนวนบันทึก: {rep['total_items']} รายการ")

    if rep.get("best_by_qty"):
        name, qty = rep["best_by_qty"]
        lines.append(f"เมนูที่ขายดีที่สุด: {name} x{qty} 🥇🍽️")
    if rep.get("best_by_revenue"):
        name_r, rev = rep["best_by_revenue"]
        lines.append(f"เมนูที่ทำเงินที่สุด: {name_r} — ฿{rev} 💰")

    lines.append("ขอบคุณทีมงานที่น่ารักทุกคนน้าาา 💖✨")
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(description="Morning sales report")
    parser.add_argument("--dry-run", action="store_true", help="Don't send Telegram, just print")
    args = parser.parse_args(argv)

    load_env()

    try:
        sheet = get_sheet()
    except Exception as exc:
        print(f"ไม่สามารถเชื่อม Google Sheets ได้: {exc}")
        return 2

    try:
        rows = sheet.get_all_values()
    except Exception as exc:
        print(f"อ่านข้อมูลจากชีทไม่สำเร็จ: {exc}")
        return 3

    rep = build_report(rows)
    msg = render_message(rep)

    if args.dry_run:
        print("--- Dry run: message to send ---")
        print(msg)
        return 0

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in environment")
        return 4

    try:
        send_telegram(token, chat_id, msg)
        print("ส่งสรุปไปที่ Telegram เรียบร้อยค่า 🎉")
    except Exception as exc:
        print(f"ส่งข้อความล้มเหลว: {exc}")
        return 5

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
