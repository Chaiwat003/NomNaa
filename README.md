# ขาหมูน้าา — ระบบช่วยรับออเดอร์หน้าร้าน 🍽️

ระบบช่วยรับและสรุปออเดอร์สำหรับร้าน "ขาหมูน้าา" โดยมุ่งเน้นการรองรับคำสั่งแบบมีเงื่อนไข (เช่น "ไม่เอาไข่ต้ม", "แยกน้ำ") และการคำนวณราคาตามท็อปปิ้ง/ตัวเลือกต่าง ๆ รวมถึงจัดข้อความสั้นสำหรับครัวเพื่อให้แม่ค้าทำงานต่อได้รวดเร็ว

## 🔎 Demo Day Checklist

- [ ] อธิบายว่าระบบทำอะไรสำหรับ domain ของนักศึกษา (ไม่ใช่ MilkLab°)
- [ ] ใส่ link ไปยัง live demo URL: https://<YOUR_DEPLOY_URL> (แทนที่ด้วย URL จริง)
- [ ] มีวิธีรันในเครื่องท้องถิ่น (local setup)
- [ ] ใส่ link ไปยัง PIVOT.md เพื่อให้ recruiter เห็น thinking process: [PIVOT.md](PIVOT.md)

## ✨ ฟีเจอร์หลัก

- Parsing คำสั่งแบบธรรมชาติ: เข้าใจคำสั่งลูกค้าที่มีเงื่อนไข (exclusions, additions)
- คำนวณราคาอัตโนมัติ: รวมราคาเมนูพื้นฐานและท็อปปิ้ง
- สรุปออเดอร์สั้นสำหรับครัว: ข้อความกระชับ เหลือบอ่านแล้วเข้าใจ
- เก็บฐานความรู้เมนู: ข้อมูลท็อปปิ้ง เวลาเปิด และราคา

## 📋 ความต้องการ

- Python 3.10 หรือใหม่กว่า
- ติดตั้ง dependencies ใน `requirements.txt`
- เก็บคีย์และไฟล์ความลับภายนอก repo (เช่น `.env`, `service-account.json`)

## 🚀 ติดตั้งและรัน (Local)

1. ติดตั้ง dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

2. ตั้งค่า environment variables (ตัวอย่าง `.env`):

```text
# ตัวอย่าง
GOOGLE_API_KEY=your_google_api_key_here
```

3. รันแอปตัวอย่าง (ถ้ามี UI แบบ Streamlit):

```powershell
# รัน Streamlit UI
c:/python312/python.exe -m streamlit run app.py

# หรือรันไฟล์ CLI
c:/python312/python.exe caption.py
```

## 🔗 Live demo

ถ้ามี Deploy URL ให้วางที่นี่ (ตัวอย่าง):

```
https://your-deploy-url.example
```

แทนที่ด้วย URL จริงก่อน Demo Day เพื่อให้ลิงก์ทำงาน

## 🗂️ โครงสร้างไฟล์ที่เกี่ยวข้อง

```
./
├── app.py           # (UI/Streamlit ถ้ามี)
├── caption.py       # ฟังก์ชันสรุปออเดอร์ / แคปชั่นตัวอย่าง
├── knowledge/       # ฐานความรู้เมนู (ข้อมูลร้าน ขาหมู)
├── requirements.txt
└── README.md
```

## 🔒 ความปลอดภัย

- อย่า commit ไฟล์ความลับ เช่น `.env` หรือ `service-account.json` ลงใน git
- หากเผลอ commit ให้รีเซ็ตคีย์และลบไฟล์จากประวัติ (เช่นใช้ `git filter-repo`)

## 📄 License

ยังไม่ระบุ

## 👨‍🍳 Author

ทีมร้านขาหมน้าา

## Demo Day Self-Check

- [ ] Deploy URL ใช้งานได้ (เปิดทดสอบล่าสุด: __________)
- [ ] ไม่มี `.env` หรือ `*.json` ใน git history
- [ ] PIVOT.md ครบ 3 ข้อ
- [ ] README อธิบายระบบของ domain ตัวเอง (ไม่ใช่ MilkLab°)
- [ ] knowledge base, prompt, UI ปรับเป็น domain ใหม่หมดแล้ว