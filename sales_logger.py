"""Simple CLI sales logger.

Usage:
  python sales_logger.py "เมนู:จำนวน:ราคา" "เมนู2:จำนวน:ราคา"

Examples:
  python sales_logger.py "PadThai:2:45" "Coke:3:15"

This script loads environment variables via python-dotenv, imports `get_sheet` from
`sheets_client`, parses CLI entries in the form เมนู:จำนวน:ราคา, computes the total,
and appends a row using `get_sheet().append_row([...])`.

Updates:
- Appends extended columns: [วันที่, เมนู, จำนวน, ราคา, ยอดรวม, ท็อปปิ้ง, ชื่อ, เบอร์]
- Creates a header row if the sheet is empty or the header doesn't match.
"""

from __future__ import annotations

import sys
import os
from datetime import datetime
from dotenv import load_dotenv

from sheets_client import get_sheet


def load_env() -> None:
	load_dotenv()


def parse_entry(entry: str) -> tuple[str, int, float]:
	parts = [p.strip() for p in entry.split(":")]
	if len(parts) != 3:
		raise ValueError(f"Invalid entry format: '{entry}'. Expected เมนู:จำนวน:ราคา")
	name = parts[0]
	try:
		qty = int(parts[1])
	except ValueError:
		raise ValueError(f"Invalid quantity in entry: '{entry}'")
	try:
		price = float(parts[2])
	except ValueError:
		raise ValueError(f"Invalid price in entry: '{entry}'")
	return name, qty, price


def format_items(items: list[tuple[str, int, float]]) -> str:
	return ", ".join(f"{name} x{qty} @{price:.2f}" for name, qty, price in items)


def main(argv: list[str] | None = None) -> int:
	argv = argv if argv is not None else sys.argv[1:]
	if not argv:
		print("Usage: python sales_logger.py \"เมนู:จำนวน:ราคา\" [\"เมนู2:จำนวน:ราคา\"]...")
		return 1

	# allow a single argument containing comma-separated entries
	raw_entries: list[str] = []
	if len(argv) == 1 and "," in argv[0]:
		raw_entries = [e.strip() for e in argv[0].split(",") if e.strip()]
	else:
		raw_entries = argv

	try:
		items = [parse_entry(e) for e in raw_entries]
	except ValueError as exc:
		print(f"Error: {exc}")
		return 2

	timestamp = datetime.now().isoformat()

	load_env()

	try:
		sheet = get_sheet()
	except Exception as exc:  # defensive: sheets_client may raise on auth
		print(f"Failed to get sheet: {exc}")
		return 3

	# Ensure header exists (extended columns)
	# Column order expected by the sheet UI: วันที่, เมนู, ท็อปปิ้ง, จำนวน, ราคา, ยอดรวม, ชื่อ, เบอร์
	header = ["วันที่", "เมนู", "ท็อปปิ้ง", "จำนวน", "ราคา", "ยอดรวม", "ชื่อ", "เบอร์"]
	try:
		values = sheet.get_all_values()
		if not values or values[0][: len(header)] != header:
			# insert header as the first row
			sheet.insert_row(header, index=1)
	except Exception as exc:
		print(f"Warning: unable to verify/insert header row: {exc}")

	# Append one row per item matching the spreadsheet columns:
	# [วันที่, เมนู, จำนวน, ราคา, ยอดรวม]
	try:
		for name, qty, price in items:
			item_total = qty * price
			# Append extended row with columns matching header order:
			# [timestamp, menu, toppings, qty, price, total, name, contact]
			row = [
				timestamp,
				name,
				"",
				str(qty),
				f"{price:.2f}",
				f"{item_total:.2f}",
				"",
				"",
			]
			sheet.append_row(row)
			print(f"Appended row: {row}")
	except Exception as exc:
		print(f"Failed to append row(s): {exc}")
		return 4

	return 0


if __name__ == "__main__":
	raise SystemExit(main())
