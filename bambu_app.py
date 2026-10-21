"""TeewangBambu.exe สำหรับแล็บที่ไม่อยากลง Python: ดับเบิลคลิกครั้งแรกตั้งค่า แล้วส่งสถานะเครื่อง Bambu เข้า teewang ไปเรื่อยๆ

  TeewangBambu.exe           ยังไม่เคยตั้งค่า = ตั้งค่าก่อน แล้วเริ่มส่ง
  TeewangBambu.exe setup     ตั้งค่าใหม่ หรือเพิ่มเครื่องที่ปิดอยู่ตอนตั้งครั้งแรก
  TeewangBambu.exe probe     สำรวจเครื่องแบบอ่านอย่างเดียว ไว้ทำส่วนนับกรัมเส้นต่องาน (bambu_probe.py) ไม่ส่งอะไรขึ้นเว็บ
  TeewangBambu.exe --check   เช็กว่าใน exe มีของครบ (ใช้ตอน build)

ค่าตั้ง (token Bambu กับกุญแจ teewang) อยู่ที่ %LOCALAPPDATA%\\TeewangBambu\\bambu.json ของเครื่องนี้ ไม่อยู่ข้าง exe
ก๊อป exe ไปให้คนอื่นแล้วรหัสไม่ติดไปด้วย
build: รีโปสาธารณะ github.com/Tayakorn000/teewang-bambu (ก๊อปไฟล์ใน agents/ ไปแล้วติด tag v* ให้ GitHub build exe แนบใน Releases)
"""

import getpass
import json
import os
import sys

import bambu
import bambu_probe
import bambu_setup

HOME = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TeewangBambu")
CFG = os.path.join(HOME, "bambu.json")
STARTUP = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "TeewangBambu.cmd")


def setup():
    os.makedirs(HOME, exist_ok=True)
    print("== ตั้งค่า: หาเครื่อง Bambu (รหัสผ่านไม่ถูกเก็บ)")
    bambu_setup.main(CFG, "IX-3D-")
    with open(CFG, encoding="utf-8") as f:
        cfg = json.load(f)
    key = ""
    while not key:
        key = getpass.getpass("กุญแจ teewang ของ INNO-X (ไม่แสดงตอนพิมพ์): ").strip()
    cfg["teewang_key"] = key
    fd = os.open(CFG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    if os.name == "nt" and input("ให้เปิดเองทุกครั้งที่เปิดเครื่องไหม [Y/n]: ").strip().lower() != "n":
        # cmd อ่านไฟล์ด้วยโค้ดเพจของเครื่อง ไม่ใช่ UTF-8 (path ที่มีภาษาไทยจะพังถ้าเขียนเป็น UTF-8)
        with open(STARTUP, "w", encoding="mbcs", newline="\r\n") as f:
            f.write(f'@start "" /min "{sys.executable}"\n')
        print(f"ตั้งให้เปิดเองแล้ว ไม่เอาแล้วลบไฟล์นี้: {STARTUP}")


def main():
    if os.name == "nt":
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    if "--check" in sys.argv:
        import paho.mqtt.client  # noqa: F401

        print("ok")
        return
    if "probe" in sys.argv[1:]:
        bambu_probe.main(CFG)
        input("เสร็จแล้ว แคปข้อความข้างบนส่งผู้ดูแลเว็บ กด Enter เพื่อปิด")
        return
    if "setup" in sys.argv[1:] or not os.path.exists(CFG):
        setup()
    with open(CFG, encoding="utf-8") as f:
        cfg = json.load(f)
    os.environ["TEEWANG_KEY"] = cfg.get("teewang_key", "")
    print(f"เริ่มส่งสถานะ {len(cfg['printers'])} เครื่องเข้า teewang ทุก {bambu.PUSH_S} วินาที ปิดหน้าต่างนี้ = หยุดส่ง", flush=True)
    bambu.main(CFG)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except SystemExit as e:
        # หน้าต่างที่ดับเบิลคลิกเปิดจะปิดทันทีถ้าไม่รอ คนจะไม่เห็นว่าพังเพราะอะไร
        if e.code not in (None, 0):
            print(e.code)
            input("กด Enter เพื่อปิด")
    except Exception as e:
        print(f"พัง: {e!r}")
        input("กด Enter เพื่อปิด")
