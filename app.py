# app.py
import os

import streamlit as st
from dotenv import load_dotenv
from google import genai
import pandas as pd
import requests
from datetime import datetime

from rag_engine import RAGEngine
from sheets_client import get_sheet
import threading
import time
import uuid
import json
import logging


def safe_rerun():
    """Call Streamlit's rerun in a way that's compatible across versions.

    Tries the public API `st.experimental_rerun()`. If that's missing, raises
    Streamlit's internal RerunException to trigger a rerun.
    """
    try:
        st.experimental_rerun()
    except Exception:
        # try internal exception paths across Streamlit versions
        for mod_path in (
            "streamlit.runtime.scriptrunner.script_runner",
            "streamlit.scriptrunner.script_runner",
            "streamlit.script_runner",
        ):
            try:
                module = __import__(mod_path, fromlist=["RerunException"]) 
                RerunException = getattr(module, "RerunException")
                raise RerunException from None
            except Exception:
                continue
        # last-resort: cannot programmatically trigger rerun in this environment.
        # Fall back to a no-op so the app doesn't crash; UI will reflect
        # session_state changes on the next user interaction.
        try:
            st.warning("Notice: Streamlit rerun not available in this environment; UI will update on next interaction.")
        except Exception:
            pass
        return

load_dotenv()
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
MODEL = "gemini-2.5-flash"

# configure simple logging for debugging Telegram interactions
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


@st.cache_resource
def load_rag():
    return RAGEngine("knowledge/khamoonaa_kb.txt")

rag = load_rag()

st.title("🐷🐖 น้องขาหมู ผู้ช่วย AI ของ Khamoonaa°")
st.caption("สั่งข้าวขาหมู สอบถามเมนู หรือดูเวลาเปิด-ปิดร้านได้เลยน้าา")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

if prompt := st.chat_input("ถามอะไรเกี่ยวกับร้านได้เลย..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)

    # RAG: Search
    context_chunks = rag.search(prompt, top_k=3)
    context = "\n---\n".join(context_chunks)

    # Generate
    full_prompt = f"""คุณคือ "น้องคากิ" ผู้ช่วย AI ของร้าน "ขาหมูน้าา" ตอบเฉพาะจากข้อมูลด้านล่าง
ถ้าไม่พบข้อมูล ให้บอกว่าไม่ทราบ อย่าแต่งข้อมูลเอง

ข้อมูลร้าน:
{context}

คำถาม: {prompt}
"""
    response = client.models.generate_content(model=MODEL, contents=full_prompt)
    answer = response.text

    st.session_state.messages.append({"role": "assistant", "content": answer})
    with st.chat_message("assistant"):
        st.write(answer)


# ----- Order form: save to Excel and Telegram alert -----
def save_order_to_excel(order: dict, filename: str = "orders.xlsx") -> bool:
    """Append order dict to an Excel file. Returns True on success."""
    try:
        df = pd.DataFrame([order])
        if os.path.exists(filename):
            try:
                existing = pd.read_excel(filename)
                combined = pd.concat([existing, df], ignore_index=True)
                combined.to_excel(filename, index=False)
            except Exception:
                # fallback: overwrite with the new row only
                df.to_excel(filename, index=False)
        else:
            df.to_excel(filename, index=False)
        return True
    except Exception:
        return False


def send_telegram_alert(order: dict, bot_token: str, chat_id: str) -> bool:
    if not bot_token or not chat_id:
        return False
    # Build a clear, line-by-line message in Thai with 2-decimal currency
    ts = order.get('timestamp', '')
    text_lines = [f"📣 New order — {ts}", ""]
    menu = order.get('menu', '-')
    toppings = order.get('toppings') or "(ไม่มี)"
    price = order.get('price')
    try:
        price_str = f"{float(price):.2f} บาท" if price is not None else "-"
    except Exception:
        price_str = str(price)

    text_lines.append(f"เมนู: {menu}")
    text_lines.append(f"ท็อปปิ้ง: {toppings}")
    text_lines.append(f"ราคา: {price_str}")
    # include explicit notes if provided
    if order.get('notes'):
        text_lines.append(f"หมายเหตุเพิ่มเติม: {order.get('notes')}")
    if order.get('customer_name'):
        text_lines.append(f"ชื่อลูกค้า: {order.get('customer_name')}")
    if order.get('customer_contact'):
        text_lines.append(f"เบอร์ติดต่อ: {order.get('customer_contact')}")

    text = "\n".join(text_lines)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
        try:
            logger.info("send_telegram_alert resp: %s %s", resp.status_code, resp.text)
        except Exception:
            pass
        return resp.ok
    except Exception:
        return False


# --- Telegram callback button handling ---
pending_callbacks = {}
listener_thread = None


def _mark_order_done_in_sheet(timestamp: str):
    try:
        sheet = get_sheet()
        headers = sheet.row_values(1)
        if "สถานะ" in headers:
            status_col = headers.index("สถานะ") + 1
        else:
            status_col = len(headers) + 1
            sheet.update_cell(1, status_col, "สถานะ")

        # find rows with matching timestamp in first column
        col1 = sheet.col_values(1)
        for idx, val in enumerate(col1, start=1):
            if val == timestamp:
                try:
                    sheet.update_cell(idx, status_col, "เสร็จแล้ว")
                except Exception:
                    continue
        return True
    except Exception:
        return False


def _telegram_update_poller(bot_token: str):
    global pending_callbacks
    offset = None
    logger.info("Telegram poller started")
    while True:
        try:
            params = {"timeout": 20}
            if offset:
                params["offset"] = offset
            # if we only care about callback queries, ask Telegram to return only those
            try:
                if pending_callbacks:
                    params["allowed_updates"] = json.dumps(["callback_query"])
            except Exception:
                pass

            resp = requests.get(f"https://api.telegram.org/bot{bot_token}/getUpdates", params=params, timeout=30)
            try:
                logger.debug("getUpdates resp: %s", resp.text)
            except Exception:
                pass
            data = resp.json()
            if not data.get("ok"):
                logger.warning("getUpdates returned not ok: %s", data)
                time.sleep(2)
                continue
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    cq = upd["callback_query"]
                    cb_data = cq.get("data")
                    if cb_data and cb_data in pending_callbacks:
                        info = pending_callbacks.pop(cb_data)
                        # acknowledge callback
                        try:
                            resp2 = requests.post(f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery", data={"callback_query_id": cq.get("id"), "text": "ออเดอร์ถูกทำเสร็จแล้ว ✅"}, timeout=10)
                            logger.info("answerCallbackQuery: %s %s", resp2.status_code, getattr(resp2, 'text', None))
                        except Exception:
                            logger.exception("answerCallbackQuery failed")
                        # edit original message to indicate done
                        try:
                            edit_text = info.get("text") + "\n\n✅ สถานะ: เสร็จแล้ว"
                            resp3 = requests.post(f"https://api.telegram.org/bot{bot_token}/editMessageText", data={"chat_id": info.get("chat_id"), "message_id": info.get("message_id"), "text": edit_text}, timeout=10)
                            logger.info("editMessageText: %s %s", resp3.status_code, getattr(resp3, 'text', None))
                        except Exception:
                            logger.exception("editMessageText failed")
                        # mark sheet rows
                        _mark_order_done_in_sheet(info.get("timestamp"))
                    else:
                        logger.warning("callback_data not found in pending_callbacks: %s", cb_data)
            time.sleep(1)
        except Exception:
            logger.exception("Exception in telegram poller loop")
            time.sleep(2)


def send_message_with_done_button(lines: list, bot_token: str, chat_id: str, order_timestamp: str):
    """Send Telegram message with an inline 'เสร็จแล้ว' button and register a callback handler."""
    global pending_callbacks, listener_thread
    token = str(uuid.uuid4())
    reply_markup = {"inline_keyboard": [[{"text": "เสร็จแล้ว", "callback_data": token}]]}
    payload = {"chat_id": chat_id, "text": "\n".join(lines), "reply_markup": json.dumps(reply_markup)}
    try:
        resp = requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", data=payload, timeout=10)
        try:
            logger.info("sendMessage resp: %s %s", resp.status_code, resp.text)
        except Exception:
            pass
        if not resp.ok:
            return False
        try:
            resj = resp.json()
        except Exception:
            logger.exception("failed to parse sendMessage response JSON")
            return False
        msg_id = resj.get("result", {}).get("message_id")
        pending_callbacks[token] = {"timestamp": order_timestamp, "message_id": msg_id, "chat_id": chat_id, "text": "\n".join(lines)}
        logger.info("registered pending callback token=%s msg_id=%s", token, msg_id)
        # start listener thread once
        if listener_thread is None or not listener_thread.is_alive():
            listener_thread = threading.Thread(target=_telegram_update_poller, args=(bot_token,), daemon=True)
            listener_thread.start()
            logger.info("started telegram listener thread")
        return True
    except Exception:
        logger.exception("send_message_with_done_button failed")
        return False


with st.expander("สั่งอาหาร (Place an order)"):
    # interactive controls (not inside a Streamlit form) so price updates immediately
    menu_items = {
        "ข้าวขาหมูเนื้อหนัง": 50,
        "ข้าวขาหมูเนื้อล้วน": 50,
        "ข้าวขาหมูคากิ": 60,
        "ขาหมูเปล่า": 100,
    }

    menu_label = st.selectbox("เลือกรายการอาหาร", options=list(menu_items.keys()))
    st.markdown("**ท็อปปิ้งเพิ่มเติม (บวกเพิ่ม)**")
    add_egg = st.checkbox("เพิ่มไข่ต้ม (+10 บาท)")
    add_gut = st.checkbox("เพิ่มไส้ (+10 บาท)")
    add_special = st.checkbox("พิเศษ (+10 บาท)")

    special = st.text_input("คำสั่งพิเศษ (เช่น ไม่เอาไข่ต้ม, แยกน้ำ)", placeholder="เช่น ไม่เอาไข่ต้ม, แยกน้ำ (ไม่มีค่าใช้จ่ายเพิ่ม)")

    # compute price live
    base_price = menu_items.get(menu_label, 0)
    toppings_price = 0
    toppings_list = []
    if add_egg:
        toppings_price += 10
        toppings_list.append("เพิ่มไข่ต้ม")
    if add_gut:
        toppings_price += 10
        toppings_list.append("เพิ่มไส้")
    if add_special:
        toppings_price += 10
        toppings_list.append("พิเศษ")

    computed_price = base_price + toppings_price
    st.metric("ราคา/ชิ้น (บาท)", f"{computed_price:} บาท")

    qty = st.number_input("จำนวน (ชิ้น)", min_value=1, value=1, step=1)
    customer = st.text_input("ชื่อลูกค้า (จำเป็น)")
    contact = st.text_input("เบอร์ติดต่อ (ไม่บังคับ)")
    notes = st.text_area("หมายเหตุเพิ่มเติม (ไม่บังคับ)")

    # initialize cart
    if "cart" not in st.session_state:
        st.session_state.cart = []

    if st.button("เพิ่มลงตะกร้า"):
        item = {
            "menu": menu_label,
            "toppings": ", ".join(toppings_list) if toppings_list else (special or "(ไม่มี)"),
            "unit_price": float(computed_price),
            "qty": int(qty),
            "total": float(computed_price) * int(qty),
            "notes": notes or special,
        }
        st.session_state.cart.append(item)
        safe_rerun()

    # show cart
    if st.session_state.cart:
        st.write("### ตะกร้า (Cart)")
        total_sum = 0.0
        for i, it in enumerate(list(st.session_state.cart)):
            cols = st.columns([4, 2, 1, 2, 1])
            cols[0].write(f"{it['menu']} — {it['toppings']}")
            cols[1].write(f"{it['unit_price']:.2f} บาท/ชิ้น")
            cols[2].write(f"x {it['qty']}")
            cols[3].write(f"{it['total']:.2f} บาท")
            if cols[4].button("ลบ", key=f"remove_{i}"):
                st.session_state.cart.pop(i)
                safe_rerun()
            total_sum += it['total']

        st.write(f"**ยอดรวมทั้งหมด:** {total_sum:.2f} บาท")

    if st.button("สั่งและบันทึกทั้งหมด"):
        if not customer or not customer.strip():
            st.error("กรุณากรอกชื่อผู้สั่งก่อนกดสั่ง")
        elif not st.session_state.cart:
            st.error("ตะกร้าว่าง ไม่มีรายการที่จะสั่ง")
        else:
            timestamp = datetime.now().strftime("%d-%m-%Y %H:%M")
            try:
                sheet = get_sheet()
                for it in st.session_state.cart:
                    row = [
                        timestamp,
                        it["menu"],
                        it.get("toppings", "(ไม่มี)"),
                            it.get("notes", "(ไม่มี)"),
                            str(it["qty"]),
                            f"{it['unit_price']:.2f}",
                            f"{it['total']:.2f}",
                            customer,
                            contact,
                    ]
                    sheet.append_row(row)
                saved = True
            except Exception as exc:
                saved = False
                st.error(f"ไม่สามารถบันทึกไปยัง Sales logger: {exc}")

            # build telegram message (clear Thai formatting, 2-decimal currency) and send with a "เสร็จแล้ว" button
            lines = [f"📣 New order — {timestamp}", ""]
            grand = 0.0
            for it in st.session_state.cart:
                toppings_text = it.get('toppings') or "(ไม่มี)"
                note_text = it.get('notes') or "(ไม่มี)"
                item_line = f"{it['qty']} x {it['menu']} ({toppings_text}) @ {it['unit_price']:.2f} บาท = {it['total']:.2f} บาท / หมายเหตุ: {note_text}"
                lines.append(item_line)
                grand += it['total']
            lines.append("")
            lines.append(f"ยอดรวมทั้งหมด: {grand:.2f} บาท")
            lines.append(f"ชื่อลูกค้า: {customer}")
            if contact:
                lines.append(f"เบอร์ติดต่อ: {contact}")

            bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
            chat_id = os.getenv("TELEGRAM_CHAT_ID")
            if bot_token and chat_id:
                alerted = send_message_with_done_button(lines, bot_token, chat_id, timestamp)
            else:
                alerted = False

            if saved and alerted:
                st.success("ออเดอร์ทั้งหมดถูกบันทึกและส่งแจ้งเตือนเรียบร้อย")
                st.session_state.cart = []
            elif saved and not alerted:
                st.warning("ออเดอร์ถูกบันทึกแล้ว แต่ไม่สามารถส่ง Telegram ได้ (ตรวจสอบ config)")
                st.session_state.cart = []
            elif not saved and alerted:
                st.warning("ไม่สามารถบันทึกออเดอร์ใน Sales logger ได้ แต่ส่ง Telegram สำเร็จ")
            else:
                st.error("ไม่สามารถบันทึกออเดอร์ได้ โปรดลองอีกครั้ง")