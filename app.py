# app.py
import os

import streamlit as st
from dotenv import load_dotenv
from google import genai
import pandas as pd
import requests
from datetime import datetime

# Set browser tab title and icon (must be called before other Streamlit calls)
st.set_page_config(page_title="น้องคากิ — Khamoonaa", page_icon="🐷")

from rag_engine import RAGEngine
from sheets_client import get_sheet
import threading
import time
import uuid
import json
import logging

# configure simple logging for debugging Telegram interactions
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def safe_rerun():
    """Call Streamlit's rerun in a way that's compatible across versions."""
    try:
        # เพิ่มคำสั่ง st.rerun() สำหรับ Streamlit เวอร์ชั่นใหม่
        st.rerun()
    except AttributeError:
        # ถ้าเป็นเวอร์ชั่นเก่า ค่อยกลับไปใช้ experimental_rerun
        try:
            st.experimental_rerun()
        except Exception:
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
            
            try:
                st.warning("Notice: Streamlit rerun not available in this environment; UI will update on next interaction.")
            except Exception:
                pass
            return

load_dotenv()
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
MODEL = "gemini-2.5-flash"

def safe_generate(prompt: str, max_retries: int = 5):
    from google.genai import errors
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(model=MODEL, contents=prompt)
        except errors.APIError as e:
            # 429 represents Too Many Requests (Rate limit), 503 is Service Unavailable / High demand, 500 / 504 are Server Errors
            if e.code in (429, 503, 504, 500) or "quota" in str(e).lower() or "UNAVAILABLE" in str(e) or "exhausted" in str(e).lower():
                wait = (2 ** attempt) + 1  # Wait 2, 3, 5, 9, 17 seconds
                logger.warning(f"Gemini API Error (Status {e.code}). รอ {wait} วินาทีแล้วลองใหม่ (ครั้งที่ {attempt+1}/{max_retries})...: {e}")
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            # Fallback for other potential transient exceptions
            err_str = str(e).lower()
            if any(k in err_str for k in ["quota", "exhausted", "503", "unavailable", "rate limit"]):
                wait = (2 ** attempt) + 1
                logger.warning(f"General Error. รอ {wait} วินาทีแล้วลองใหม่ (ครั้งที่ {attempt+1}/{max_retries})...: {e}")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("เกินจำนวนการลองใหม่สูงสุด (max retries exceeded)")

@st.cache_resource
def load_rag():
    return RAGEngine("knowledge/khamoonaa_kb.txt")

rag = load_rag()

if "messages" not in st.session_state:
    st.session_state.messages = []

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
        price_str = f"{int(round(float(price)))} บาท" if price is not None else "-"
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


class TelegramPollerManager:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.pending_callbacks = {}
        self.thread = None
        self.running = False

    def register_callback(self, token: str, info: dict):
        self.pending_callbacks[token] = info

    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self.running = True
            self.thread = threading.Thread(target=self._poller_loop, daemon=True)
            self.thread.start()
            logger.info("Telegram poller thread started successfully.")

    def _poller_loop(self):
        offset = None
        while self.running:
            try:
                params = {"timeout": 15}
                if offset:
                    params["offset"] = offset

                resp = requests.get(f"https://api.telegram.org/bot{self.bot_token}/getUpdates", params=params, timeout=25)
                if not resp.ok:
                    time.sleep(2)
                    continue

                data = resp.json()
                if not data.get("ok"):
                    time.sleep(2)
                    continue

                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    if "callback_query" in upd:
                        cq = upd["callback_query"]
                        cb_data = cq.get("data")
                        if cb_data and cb_data in self.pending_callbacks:
                            info = self.pending_callbacks.pop(cb_data)

                            # 1. Acknowledge callback immediately in background thread for instant response!
                            try:
                                requests.post(f"https://api.telegram.org/bot{self.bot_token}/answerCallbackQuery", data={"callback_query_id": cq.get("id"), "text": "ออเดอร์ถูกทำเสร็จแล้ว ✅"}, timeout=5)
                            except Exception:
                                pass

                            # 2. Edit message text immediately in background thread
                            try:
                                edit_text = info.get("text") + "\n\n✅ สถานะ: เสร็จแล้ว"
                                requests.post(f"https://api.telegram.org/bot{self.bot_token}/editMessageText", data={"chat_id": info.get("chat_id"), "message_id": info.get("message_id"), "text": edit_text}, timeout=5)
                            except Exception:
                                pass

                            # 3. Update Sheets in background thread without blocking the callback responses!
                            threading.Thread(target=_mark_order_done_in_sheet, args=(info.get("timestamp"),), daemon=True).start()

            except Exception as e:
                logger.exception("Exception in telegram poller loop")
                time.sleep(2)

@st.cache_resource
def get_poller_manager(bot_token: str):
    manager = TelegramPollerManager(bot_token)
    manager.start()
    return manager

def send_message_with_done_button(lines: list, bot_token: str, chat_id: str, order_timestamp: str):
    token = str(uuid.uuid4())
    
    # 1. ทำความสะอาด Token และ Chat ID
    clean_token = bot_token.strip()
    clean_chat_id = chat_id.strip()
    
    reply_markup = {"inline_keyboard": [[{"text": "เสร็จแล้ว", "callback_data": token}]]}
    payload = {"chat_id": clean_chat_id, "text": "\n".join(lines), "reply_markup": reply_markup}
    url = f"https://api.telegram.org/bot{clean_token}/sendMessage"
    
    # 2. ระบบลองส่งใหม่ (Retry) สูงสุด 3 ครั้งเมื่อเน็ตหลุด
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            # ขยายเวลาเป็น 30 วินาที
            resp = requests.post(url, json=payload, timeout=30)
            
            try:
                logger.info("sendMessage resp: %s %s", resp.status_code, resp.text)
            except Exception:
                pass

            if not resp.ok:
                return f"Telegram API Error: {resp.status_code}: {resp.text}"

            try:
                resj = resp.json()
            except Exception:
                return f"Telegram API Error: unable to parse JSON response: {resp.text}"

            msg_id = resj.get("result", {}).get("message_id")
            if not msg_id:
                return f"Telegram API Error: missing message_id in response: {resj}"

            # Register callback using singleton poller manager
            manager = get_poller_manager(clean_token)
            manager.register_callback(token, {
                "timestamp": order_timestamp,
                "message_id": msg_id,
                "chat_id": clean_chat_id,
                "text": "\n".join(lines),
            })

            return True

        except requests.exceptions.RequestException as e:
            # รอเวลาเพิ่มขึ้นเรื่อยๆ แล้วลองใหม่ (Exponential backoff)
            if attempt == max_attempts - 1:
                return f"Network Error: {str(e)}"
            backoff = 2 ** attempt
            time.sleep(backoff)

        except Exception as e:
            return f"System Error: {str(e)}" 

# ==================== PREMIUM UI DESIGN & LAYOUT ====================
# 1. Custom CSS Theme Injections
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Kanit:wght@300;400;500;600;700&family=Outfit:wght@300;400;500;600;700&display=swap');
    
    html, body, [class*="css"], .stApp {
        font-family: 'Kanit', 'Outfit', sans-serif !important;
    }
    
    /* Main Background Warmth */
    .stApp {
        background: linear-gradient(135deg, #FFFDFB 0%, #FFF5EF 100%) !important;
    }
    
    /* Sidebar Warm Tone Styling */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #FFFDFB 0%, #FFF5EF 100%) !important;
        border-right: 1px solid #FFE2D4 !important;
    }
    
    /* Premium Header Container */
    .header-container {
        text-align: center;
        background: rgba(255, 255, 255, 0.7);
        backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 222, 206, 0.6);
        border-radius: 24px;
        padding: 24px;
        box-shadow: 0 10px 30px rgba(226, 94, 62, 0.04);
        margin-bottom: 25px;
    }
    .header-title {
        color: #E25E3E;
        font-family: 'Kanit', sans-serif;
        font-weight: 700;
        font-size: 2.2rem;
        margin-top: 10px;
        margin-bottom: 5px;
    }
    .header-subtitle {
        color: #8C5240;
        font-size: 1.05rem;
        margin-bottom: 12px;
    }
    .shop-info-badge {
        display: inline-block;
        background: linear-gradient(90deg, #FF7E5F, #FEB47B);
        color: white;
        padding: 6px 18px;
        border-radius: 30px;
        font-weight: 500;
        font-size: 0.9rem;
        box-shadow: 0 4px 15px rgba(255, 126, 95, 0.2);
    }

    /* Style the native Streamlit containers */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background: rgba(255, 255, 255, 0.75) !important;
        backdrop-filter: blur(10px) !important;
        border: 1px solid rgba(255, 222, 206, 0.45) !important;
        border-radius: 24px !important;
        padding: 24px !important;
        box-shadow: 0 8px 32px rgba(226, 94, 62, 0.02) !important;
        margin-bottom: 20px !important;
    }
    
    .section-title {
        font-size: 1.45rem;
        font-weight: 600;
        color: #E25E3E;
        margin-bottom: 15px;
        display: flex;
        align-items: center;
        gap: 10px;
        border-bottom: 2px solid #FFE9DF;
        padding-bottom: 8px;
    }

    /* Product Cards */
    .product-card {
        background: white;
        border-radius: 16px;
        padding: 16px;
        border: 1px solid #FFEBE2;
        box-shadow: 0 4px 12px rgba(0,0,0,0.01);
        margin-bottom: 12px;
        transition: all 0.2s ease-in-out;
    }
    .product-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 18px rgba(226, 94, 62, 0.06);
        border-color: #FFD4C2;
    }
    .product-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .product-title {
        font-weight: 600;
        font-size: 1.1rem;
        color: #4A2E2B;
    }
    .product-price {
        font-weight: 700;
        font-size: 1.15rem;
        color: #E25E3E;
    }
    .product-desc {
        color: #8A7A78;
        font-size: 0.85rem;
        margin-top: 4px;
    }

    /* Premium inputs and buttons */
    div.stButton > button {
        background: linear-gradient(135deg, #FF7E5F 0%, #FEB47B 100%) !important;
        color: white !important;
        border-radius: 12px !important;
        border: none !important;
        padding: 8px 20px !important;
        font-weight: 600 !important;
        box-shadow: 0 4px 15px rgba(255, 126, 95, 0.2) !important;
        transition: all 0.2s ease !important;
        width: 100%;
    }
    div.stButton > button:hover {
        transform: translateY(-1px) !important;
        box-shadow: 0 6px 18px rgba(255, 126, 95, 0.3) !important;
    }
    div.stButton > button:active {
        transform: translateY(0px) !important;
    }
    
    /* Metrics Override */
    [data-testid="stMetricValue"] {
        font-family: 'Outfit', sans-serif !important;
        font-weight: 700 !important;
        color: #E25E3E !important;
    }
    
    /* Custom spacing */
    .stChatInputContainer {
        border-radius: 16px !important;
        border: 1px solid #FFE2D4 !important;
    }
    
    /* FAQ Quick Pill Buttons in Main Body */
    .stApp [data-testid="main"] div[data-testid="stButton"] button {
        background: transparent !important;
        color: #8C5240 !important;
        border: 1.5px solid #FFD4C2 !important;
        border-radius: 30px !important;
        padding: 4px 16px !important;
        font-size: 0.88rem !important;
        font-weight: 500 !important;
        box-shadow: none !important;
        transition: all 0.2s ease !important;
        width: 100% !important;
        margin-bottom: 6px !important;
    }
    .stApp [data-testid="main"] div[data-testid="stButton"] button:hover {
        background: #FFEBE2 !important;
        color: #E25E3E !important;
        border-color: #E25E3E !important;
        transform: translateY(-1px) !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# Header Section
import base64
def get_base64_image(image_path):
    if os.path.exists(image_path):
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode()
    return ""

img_b64 = get_base64_image("khamoonaa_mascot.png")
if img_b64:
    img_html = f'<img src="data:image/png;base64,{img_b64}" width="160" style="border-radius: 50%; box-shadow: 0 8px 24px rgba(226, 94, 62, 0.12);">'
else:
    img_html = '<div style="font-size: 3rem;">🐷</div>'

st.markdown(
    f"""
    <div class="header-container">
        <div style="margin-bottom: 15px;">{img_html}</div>
        <div class="header-title">🐷 น้องคากิ ผู้ช่วย AI ของ Khamoonaa°</div>
        <div class="header-subtitle">สั่งอาหารออนไลน์ สอบถามเมนู หรือคุยกับน้องคากิได้เลยน้าา</div>
        <span class="shop-info-badge">⏰ เปิดให้บริการ: 17:00 – 02:00 น. ทุกวัน • หน้าบ้านแถวหลังมอ</span>
    </div>
    """,
    unsafe_allow_html=True
)

# ----- SIDEBAR: Digital Menu & Cart Ordering -----
with st.sidebar:
    st.markdown('<div class="section-title">🥩 เมนูดิจิทัลสุดอร่อย</div>', unsafe_allow_html=True)
    
    st.markdown(
        """
        <div class="product-card">
            <div class="product-header">
                <span class="product-title">🍖 ข้าวขาหมูเนื้อหนัง</span>
                <span class="product-price">50 ฿</span>
            </div>
            <div class="product-desc">สูตรเด็ดเนื้อนุ่มละมุนลิ้น พร้อมหนังขาหมูพะโล้สุดฟิน มีไข่ต้มให้ครึ่งซีก (ธรรมดา)</div>
        </div>
        <div class="product-card">
            <div class="product-header">
                <span class="product-title">🥩 ข้าวขาหมูเนื้อล้วน</span>
                <span class="product-price">50 ฿</span>
            </div>
            <div class="product-desc">เนื้อล้วนๆ นุ่มๆ แบบไม่มีหนัง สำหรับคนรักสุขภาพ มีไข่ต้มให้ครึ่งซีก</div>
        </div>
        <div class="product-card">
            <div class="product-header">
                <span class="product-title">🏆 ข้าวขาหมูคากิ</span>
                <span class="product-price">60 ฿</span>
            </div>
            <div class="product-desc">คากิส่วนที่นุ่ม คอลลาเจนเน้นๆ เอ็นกรุบละลายในปาก เมนูแนะนำแนะนำ!</div>
        </div>
        <div class="product-card">
            <div class="product-header">
                <span class="product-title">🍲 ขาหมูเปล่า (กับข้าว)</span>
                <span class="product-price">100 ฿</span>
            </div>
            <div class="product-desc">ขาหมูเป็นกับข้าวตักเน้นๆ สำหรับแบ่งทานเป็นครอบครัว แฟนพันธุ์แท้ต้องจัด!</div>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.markdown("<b style='color: #4A2E2B;'>📝 เลือกความอร่อยและสั่งอาหาร:</b>", unsafe_allow_html=True)
    menu_items = {
        "ข้าวขาหมูเนื้อหนัง": 50,
        "ข้าวขาหมูเนื้อล้วน": 50,
        "ข้าวขาหมูคากิ": 60,
        "ขาหมูเปล่า": 100,
    }

    menu_label = st.selectbox("เลือกเมนูหลัก", options=list(menu_items.keys()))
    
    st.markdown("<small style='color: #4A2E2B; font-weight:600;'>ท็อปปิ้งเพิ่มเติม (บวกเพิ่ม)</small>", unsafe_allow_html=True)
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        add_egg = st.checkbox("🍳 เพิ่มไข่ต้ม (+10 บาท)")
        add_special = st.checkbox("⭐ พิเศษ (+10 บาท)")
    with col_t2:
        add_gut = st.checkbox("🥖 เพิ่มไส้ (+10 บาท)")
        
    special = st.text_input("คำสั่งพิเศษอื่นๆ (เช่น ไม่เอาผัก, แยกน้ำ)", placeholder="ระบุตรงนี้ได้เลย...")

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
    
    col_pr, col_qt = st.columns(2)
    with col_pr:
        st.metric("ราคาต่อชิ้น", f"{int(round(computed_price))} บาท")
    with col_qt:
        qty = st.number_input("จำนวน (ชิ้น)", min_value=1, value=1, step=1)
        
    customer = st.text_input("ชื่อลูกค้า (จำเป็นต้องระบุ)", placeholder="กรุณากรอกชื่อของคุณ...")
    contact = st.text_input("เบอร์ติดต่อ (จำเป็น)", placeholder="เช่น 089-XXXXXXX (จำเป็น)")
    notes = st.text_area("รายละเอียดจัดส่ง / หมายเหตุเพิ่มเติม", placeholder="ระบุรายละเอียดหรือหมายเหตุ...")

    # initialize cart
    if "cart" not in st.session_state:
        st.session_state.cart = []

    if st.button("🛒 เพิ่มลงตะกร้า"):
        item = {
            "menu": menu_label,
            "toppings": ", ".join(toppings_list) if toppings_list else (special or "(ไม่มี)"),
            "unit_price": float(computed_price),
            "qty": int(qty),
            "total": float(computed_price) * int(qty),
            "notes": notes or special,
        }
        st.session_state.cart.append(item)
        st.success(f"เพิ่ม {menu_label} ลงตะกร้าแล้ว! 🐷")
        time.sleep(0.5)
        safe_rerun()

    # show cart
    if st.session_state.cart:
        st.markdown("<hr style='border-color: #FFE6DA; margin: 15px 0;'>", unsafe_allow_html=True)
        st.markdown("<b style='color: #4A2E2B; font-size:1.15rem;'>🛍️ ตะกร้าสินค้าของคุณ</b>", unsafe_allow_html=True)
        total_sum = 0.0
        
        for i, it in enumerate(list(st.session_state.cart)):
            col_item, col_p_q, col_del = st.columns([5, 3, 2])
            with col_item:
                st.markdown(f"**{it['menu']}**")
                st.markdown(f"<small style='color: #8A7A78;'>ท็อปปิ้ง: {it['toppings']}</small>", unsafe_allow_html=True)
                if it.get("notes") and it["notes"] != it["toppings"]:
                    st.markdown(f"<small style='color: #E25E3E;'>หมายเหตุ: {it['notes']}</small>", unsafe_allow_html=True)
            with col_p_q:
                st.markdown(f"<small style='color: #8A7A78;'>{int(round(it['unit_price']))} บ. x {it['qty']}</small>", unsafe_allow_html=True)
                st.markdown(f"**รวม {int(round(it['total']))} บาท**")
            with col_del:
                if st.button("ลบออก", key=f"remove_{i}"):
                    st.session_state.cart.pop(i)
                    safe_rerun()
            total_sum += it['total']
            st.markdown("<div style='height:1px; background:#FFF0EB; margin:8px 0;'></div>", unsafe_allow_html=True)

        st.markdown(
            f"""
            <div style='background: rgba(226, 94, 62, 0.08); border-radius: 12px; padding: 12px; text-align: center; margin: 15px 0;'>
                <span style='font-size: 1.05rem; color: #4A2E2B;'>ยอดรวมทั้งหมด:</span>
                <span style='font-size: 1.5rem; font-weight: 700; color: #E25E3E;'> {int(round(total_sum))} บาท</span>
            </div>
            """,
            unsafe_allow_html=True
        )

        if st.button("🚀 ยืนยันการสั่งอาหารและส่งออเดอร์"):
            if not customer or not customer.strip():
                st.error("กรุณากรอกชื่อผู้สั่งก่อนกดสั่ง")
            elif not contact or not contact.strip():
                st.error("กรุณากรอกเบอร์ติดต่อสำหรับส่งข้อมูลการสั่งซื้อด้วยนะคะ")
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
                            f"{it['unit_price']}",
                            f"{it['total']}",
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
                    item_line = f"{it['qty']} x {it['menu']} ({toppings_text}) @ {int(round(it['unit_price']))} บาท = {int(round(it['total']))} บาท / หมายเหตุ: {note_text}"
                    lines.append(item_line)
                    grand += it['total']
                lines.append("")
                lines.append(f"ยอดรวมทั้งหมด: {int(round(grand))} บาท")
                lines.append(f"ชื่อลูกค้า: {customer}")
                if contact:
                    lines.append(f"เบอร์ติดต่อ: {contact}")

                bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
                chat_id = os.getenv("TELEGRAM_CHAT_ID")
                if bot_token and chat_id:
                    alerted = send_message_with_done_button(lines, bot_token, chat_id, timestamp)
                else:
                    alerted = "หาค่า Token หรือ Chat ID ไม่เจอใน Hugging Face Secrets"

                if saved and alerted is True:
                    st.success("ออเดอร์ทั้งหมดถูกบันทึกและส่งแจ้งเตือนเข้า Telegram เรียบร้อย! 🐷")
                    st.session_state.cart = []
                    time.sleep(1.5)
                    safe_rerun()
                elif saved and alerted is not True:
                    st.warning(f"ออเดอร์บันทึกลงชีตแล้ว แต่ส่ง Telegram ไม่ได้ ❌ สาเหตุ: {alerted}")
                    st.session_state.cart = []
                    time.sleep(1.5)
                    safe_rerun()
                elif not saved and alerted is True:
                    st.warning("ไม่สามารถบันทึกออเดอร์ในชีตได้ แต่ส่ง Telegram สำเร็จ")
                else:
                    st.error("ไม่สามารถบันทึกออเดอร์ และไม่สามารถส่งแจ้งเตือนได้เลย")


# ----- MAIN BODY: AI Chatbot -----
with st.container(border=True):
    st.markdown('<div class="section-title">💬 ถามตอบกับน้องคากิ</div>', unsafe_allow_html=True)
    
    chat_placeholder = st.container(height=450)
    with chat_placeholder:
        if not st.session_state.messages:
            st.markdown(
                """
                <div style='text-align: center; color: #8A7A78; padding-top: 60px;'>
                    <div style='font-size: 3rem; margin-bottom: 12px;'>🐷</div>
                    <b>ยินดีต้อนรับสู่ร้าน ขาหมูน้าา!</b><br>
                    สามารถพิมพ์สอบถามเมนู วันเวลาเปิด-ปิด หรือสั่งอาหารได้เลยค่ะ<br>
                    เช่น <i>"ร้านปิดกี่โมง?"</i> หรือ <i>"มีเมนูอะไรบ้าง?"</i>
                </div>
                """,
                unsafe_allow_html=True
            )
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])
                
    # FAQ quick buttons below the chat but above the text input
    st.markdown("<small style='color: #8A7A78; font-weight:600;'>💡 คำถามที่พบบ่อย (แตะเพื่อถาม):</small>", unsafe_allow_html=True)
    col_b1, col_b3 = st.columns(2)
    col_b4, col_b5 = st.columns(2)
    
    clicked_question = None
    with col_b1:
        if st.button("🕒 ร้านเปิดกี่โมง", key="faq_open"):
            clicked_question = "ร้านเปิดกี่โมง"
    with col_b3:
        if st.button("✨ มีเมนูแนะนำอะไร", key="faq_menu"):
            clicked_question = "มีเมนูแนะนำอะไร"
    with col_b4:
        if st.button("📍 ร้านอยู่ที่ไหน", key="faq_loc"):
            clicked_question = "ร้านอยู่ที่ไหน"
    with col_b5:
        if st.button("📞 ติดต่อร้านได้ทางไหน", key="faq_contact"):
            clicked_question = "ติดต่อร้านได้ทางไหน"

    # Capture prompt or quick button
    final_prompt = None
    if prompt := st.chat_input("คุยกับน้องคากิตรงนี้ได้เลย..."):
        final_prompt = prompt
    elif clicked_question:
        final_prompt = clicked_question

    if final_prompt:
        st.session_state.messages.append({"role": "user", "content": final_prompt})
        with chat_placeholder:
            with st.chat_message("user"):
                st.write(final_prompt)
        
        # RAG: Search
        context_chunks = rag.search(final_prompt, top_k=3)
        context = "\\n---\\n".join(context_chunks)
        
        # Generate
        full_prompt = f"""คุณคือ "น้องคากิ" ผู้ช่วย AI ของร้าน "ขาหมูน้าา" ตอบเฉพาะจากข้อมูลด้านล่าง
ถ้าไม่พบข้อมูล ให้บอกว่าไม่ทราบ อย่าแต่งข้อมูลเอง

ข้อมูลร้าน:
{context}

คำถาม: {final_prompt}
"""
        response = safe_generate(full_prompt)
        answer = response.text
        
        st.session_state.messages.append({"role": "assistant", "content": answer})
        with chat_placeholder:
            with st.chat_message("assistant"):
                st.write(answer)
                
        safe_rerun()
