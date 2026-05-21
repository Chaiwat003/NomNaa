"""Generate a morning sales report for ร้านข้าวขาหมู and send to Telegram.

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
    if not dt_str:
        return None
    dt_str = dt_str.strip()
    # try ISO first
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        pass

    # common fallbacks
    fmts = [
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for f in fmts:
        try:
            return datetime.strptime(dt_str, f)
        except Exception:
            continue

    # try stripping timezone Z
    try:
        if dt_str.endswith("Z"):
            return datetime.fromisoformat(dt_str[:-1])
    except Exception:
        pass

    return None


def build_report(rows: list[list[str]]) -> dict:
    """Rows include header row. Returns a dict with totals and top menus."""
    if not rows:
        return {}

    header = rows[0]
    data = rows[1:]

    # normalize header names to find columns (support Thai headers from sheet)
    hdr_map: dict[str, int] = {}
    for i, h in enumerate(header):
        if h is None:
            continue
        key = str(h).strip().lower()
        hdr_map[key] = i

    def find_col(*candidates: str) -> int | None:
        for cand in candidates:
            cand = cand.lower()
            for k, idx in hdr_map.items():
                if cand in k:
                    return idx
        return None

    # possible header keywords
    date_col = find_col("date", "วันที่", "time")
    name_col = find_col("เมนู", "menu", "ชื่อเมนู")
    qty_col = find_col("จำนวน", "qty", "จำนวนชิ้น")
    price_col = find_col("ราคา", "price")
    total_col = find_col("ยอดรวม", "ยอด", "total")

    # fallback to first 5 columns if mapping failed
    if date_col is None or name_col is None or qty_col is None:
        date_col, name_col, qty_col, price_col, total_col = 0, 1, 2, 3, 4

    yesterday = (datetime.now() - timedelta(days=1)).date()

    total_sales = 0.0
    total_items = 0
    menu_qty: dict[str, int] = defaultdict(int)
    menu_revenue: dict[str, float] = defaultdict(float)

    for r in data:
        # skip empty rows
        if not any(r):
            continue
        # safely get columns
        def get(i: int) -> str:
            try:
                return str(r[i]).strip()
            except Exception:
                return ""

        date_str = get(date_col) if date_col is not None else ""
        name = get(name_col) if name_col is not None else ""
        qty_str = get(qty_col) if qty_col is not None else ""
        price_str = get(price_col) if price_col is not None else ""
        total_str = get(total_col) if total_col is not None else ""

        dt = parse_iso(date_str)
        if not dt:
            continue
        if dt.date() != yesterday:
            continue

        try:
            qty = int(float(qty_str))
        except Exception:
            # try to extract digits
            try:
                qty = int(''.join(ch for ch in qty_str if ch.isdigit()))
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
        total_items += qty
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
        return "ไม่มีข้อมูลของเมื่อวานสำหรับร้านข้าวขาหมูเลยค่า 😭\nพักผ่อนเยอะๆ นะคะ แล้วค่อยเริ่มใหม่พรุ่งนี้นะ 💪💕"

    lines = []

    lines.append(f"สรุปยอดร้านข้าวขาหมูเมื่อวานน้าาา 🐷🍚 \nวันที่: {rep['date']}")
    lines.append(f"ยอดรวม: ฿{rep['total_sales']:.2f} 💸")
    lines.append(f"จำนวนจานที่ขายรวม: {rep['total_items']} จาน")


    if rep.get("best_by_qty"):
        name, qty = rep["best_by_qty"]
        lines.append(f"เมนูที่ขายดีที่สุด: {name} x{qty} จาน 🥇🍽️")
    if rep.get("best_by_revenue"):
        name_r, rev = rep["best_by_revenue"]
        lines.append(f"เมนูที่ทำเงินที่สุด: {name_r} — ฿{rev} 💰")

    # Per-menu breakdown (sorted by revenue desc)
    menu_qty = rep.get("menu_qty", {})
    menu_rev = rep.get("menu_revenue", {})
    if menu_qty:
        lines.append("\nรายละเอียดตามเมนู:")
        for name, qty in sorted(menu_qty.items(), key=lambda x: menu_rev.get(x[0], 0), reverse=True):
            rev = menu_rev.get(name, 0.0)
            lines.append(f"- {name}: {qty} จาน — ฿{rev:.2f}")

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
