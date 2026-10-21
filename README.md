# TeewangBambu

ส่งสถานะเครื่องปริ้น 3 มิติ Bambu Lab ของแล็บ (ว่าง กำลังปริ้นกี่ % เหลือกี่นาที) ขึ้นเว็บ [ที่ว่าง](https://teewang.taokaetech.com) ให้นักศึกษาดูว่าเครื่องไหนว่าง

อ่านสถานะอย่างเดียว ไม่สั่งเครื่อง ไม่ต้องเปิด Developer Mode

## โหลด

[TeewangBambu.exe](https://github.com/Tayakorn000/teewang-bambu/releases/latest/download/TeewangBambu.exe) (Windows)

## ใช้

1. ดับเบิลคลิก `TeewangBambu.exe` ครั้งแรกจะถาม
   - ต่อตรงกับเครื่องในวงเดียวกันไหม (แนะนำ ไม่ต้องใช้บัญชี) โปรแกรมหาเครื่องที่เปิดอยู่ในวงเอง ใช้ access code ที่ Bambu Studio ในเครื่องนี้จำไว้ เครื่องไหนไม่มีให้พิมพ์จากหน้าจอเครื่อง (ตั้งค่า > เครือข่าย) Windows ถามเรื่อง firewall ให้กดอนุญาต
   - หรือผ่านบัญชี Bambu: ใช้การล็อกอินที่ค้างใน Bambu Studio ถ้าอ่านได้ ไม่งั้นถามอีเมลกับรหัสผ่าน
   - กุญแจ teewang ของหน่วยงาน (ขอจากผู้ดูแลเว็บ)
   - ให้เปิดเองทุกครั้งที่เปิดเครื่องไหม
2. โปรแกรมขึ้นรายชื่อเครื่องในบัญชีกับรหัสบนเว็บที่จับคู่ให้ (จากเลขในชื่อเครื่อง) แล้วเริ่มส่งทุก 30 วินาที บัญชีเดียวส่งได้ทุกเครื่องในบัญชี
3. เปิดหน้าต่างทิ้งไว้ ปิดหน้าต่าง = หยุดส่ง

ตั้งค่าใหม่หรือเพิ่มเครื่องที่ปิดอยู่ตอนตั้งครั้งแรก: `TeewangBambu.exe setup` (แบบในวง เครื่องที่ตั้งไว้แล้วไม่หาย)

## ข้อมูลอยู่ไหน

- รหัสผ่านบัญชี Bambu ไม่ถูกเก็บ ใช้ตอนล็อกอินครั้งเดียว
- token ของบัญชีกับกุญแจ teewang อยู่ที่ `%LOCALAPPDATA%\TeewangBambu\bambu.json` ของเครื่องนั้น ไม่อยู่ข้าง exe อย่าส่งไฟล์นี้ต่อ
- ที่ส่งขึ้นเว็บมีแค่รหัสเครื่อง รุ่น สถานะ เปอร์เซ็นต์ และเวลาที่เหลือ

## รันจากซอร์ส (Mac, Linux, Raspberry Pi)

```bash
pip install "paho-mqtt>=2,<3"
python3 bambu_setup.py                       # สร้าง bambu.json แบบผ่านบัญชี Bambu
TEEWANG_KEY=... python3 bambu.py bambu.json  # ส่งไปเรื่อยๆ
```

ต่อเครื่องตรงในวง LAN แทนบัญชีก็ได้ ดูรูปแบบ `bambu.json` ที่หัวไฟล์ `bambu.py`

## หมายเหตุ

- ข้อความจากเครื่องกับขั้นล็อกอินเขียนตามที่ [OpenBambuAPI](https://github.com/Doridian/OpenBambuAPI) และ [ha-bambulab](https://github.com/greghesp/ha-bambulab) แกะไว้ ไม่ใช่ API ทางการของ Bambu Lab
- build exe เองได้บนเครื่อง Linux ที่มี Docker: `./build.sh` (PyInstaller ของ Windows รันใน Wine)
- exe รวม [paho-mqtt](https://github.com/eclipse-paho/paho.mqtt.python) (EPL-2.0 / EDL-1.0) และ build ด้วย PyInstaller
