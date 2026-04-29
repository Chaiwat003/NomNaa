"""
Instagram Caption Generator for Nomnaa Cafe
สร้างแคปชั่นติดต่างแบบ 3 รูปแบบ (cute, minimal, Gen-Z) โดยใช้ Google Gemini API
"""

import os
from typing import Dict
from dotenv import load_dotenv
import google.generativeai as genai

# โหลด environment variables จากไฟล์ .env
load_dotenv()


def init_gemini() -> None:
    """เริ่มต้นการใช้ Gemini API ด้วย API key จาก environment"""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("ไม่พบ GOOGLE_API_KEY ในตัวแปร environment")
    genai.configure(api_key=api_key)


def generate_captions(menu_name: str, price: float) -> Dict[str, str]:
    """
    สร้างแคปชั่น Instagram 3 แบบสำหรับรายการเมนู
    
    Args:
        menu_name: ชื่อรายการเมนู
        price: ราคาของรายการเมนู (บาท)
    
    Returns:
        พจนานุกรมที่มีคีย์ 'Cute', 'Minimal', 'Gen-Z' พร้อมแคปชั่น
    
    Raises:
        ValueError: หากไม่พบ GOOGLE_API_KEY
    """
    init_gemini()
    
    model = genai.GenerativeModel("gemini-2.5-flash")
    
    prompt = f"""
    สร้างแคปชั่น Instagram 3 แบบสำหรับรายการเมนูคาเฟ่
    ชื่อเมนู: {menu_name}
    ราคา: {price} บาท
    
    โปรดสร้างแคปชั่น 3 แบบในรูปแบบต่อไปนี้:
    1. Cute: เป็นกันเอง สนุกสนาน มีอีโมจิ น้ำเสียงสบาย ๆ
    2. Minimal: ง่าย สวยหรู ดูเรียบร้อย ใช้คำไม่มากนัก
    3. Gen-Z: ทันสมัย ตลกขบขัน เข้าใจง่าย ใช้สแลงสมัยใหม่ (แต่เหมาะสม)
    
    ให้ตอบในรูปแบบนี้:
    Cute: [แคปชั่นที่นี่]
    Minimal: [แคปชั่นที่นี่]
    Gen-Z: [แคปชั่นที่นี่]
    """
    
    response = model.generate_content(prompt)
    
    # แยกแคปชั่นจากการตอบกลับ
    captions = parse_captions(response.text)
    return captions


def parse_captions(response_text: str) -> Dict[str, str]:
    """
    แยกแคปชั่นจากการตอบกลับของโมเดล
    
    Args:
        response_text: การตอบกลับดิบจาก Gemini
    
    Returns:
        พจนานุกรมที่มีรูปแบบแคปชั่นเป็นคีย์และแคปชั่นเป็นค่า
    """
    captions = {
        "Cute": "",
        "Minimal": "",
        "Gen-Z": ""
    }
    
    lines = response_text.strip().split("\n")
    
    for line in lines:
        line = line.strip()
        if line.startswith("Cute:"):
            captions["Cute"] = line.replace("Cute:", "").strip()
        elif line.startswith("Minimal:"):
            captions["Minimal"] = line.replace("Minimal:", "").strip()
        elif line.startswith("Gen-Z:"):
            captions["Gen-Z"] = line.replace("Gen-Z:", "").strip()
    
    return captions


def main() -> None:
    """ฟังก์ชันหลักสำหรับสาธิตการสร้างแคปชั่น"""
    # รับข้อมูลจากผู้ใช้
    menu_name = input("🍽️  ป้อนชื่อเมนู: ")
    price_input = input("💰 ป้อนราคา (บาท): ")
    
    try:
        price = float(price_input)
    except ValueError:
        print("❌ ราคาต้องเป็นตัวเลข")
        return
    
    print(f"\n🎨 กำลังสร้างแคปชั่นสำหรับ: {menu_name} ({price} บาท)\n")
    
    captions = generate_captions(menu_name, price)
    
    print("=" * 50)
    print(f"📝 Cute:")
    print(f"{captions['Cute']}\n")
    
    print("=" * 50)
    print(f"✨ Minimal:")
    print(f"{captions['Minimal']}\n")
    
    print("=" * 50)
    print(f"🚀 Gen-Z:")
    print(f"{captions['Gen-Z']}\n")
    print("=" * 50)


if __name__ == "__main__":
    main()
