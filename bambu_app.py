"""TeewangBambu.exe สำหรับแล็บที่ไม่อยากลง Python: ดับเบิลคลิกครั้งแรกตั้งค่า แล้วส่งสถานะเครื่อง Bambu เข้า teewang ไปเรื่อยๆ

  TeewangBambu.exe            ยังไม่เคยตั้งค่า = ตั้งค่าก่อน แล้วถามว่าฝังลงเครื่องไหม ติดตั้งแล้ว = บอกสถานะ
  TeewangBambu.exe setup      ตั้งค่าใหม่ หรือเพิ่มเครื่องที่ปิดอยู่ตอนตั้งครั้งแรก
  TeewangBambu.exe install    ฝังลงเครื่อง: ทำงานเบื้องหลังตั้งแต่เปิดเครื่อง ไม่ต้องล็อกอิน ไม่มีหน้าต่าง (ขอสิทธิ์ผู้ดูแล)
  TeewangBambu.exe uninstall  เอาออก
  TeewangBambu.exe probe      สำรวจเครื่องแบบอ่านอย่างเดียว (bambu_probe.py) ไม่ส่งอะไรขึ้นเว็บ
  TeewangBambu.exe run        ส่งไปเรื่อยๆ แบบไม่มีคนดู (ที่ task เรียก) เขียน log แทนหน้าจอ
  TeewangBambu.exe --check    เช็กว่าใน exe มีของครบ (ใช้ตอน build)

ค่าตั้ง (access code / token กับกุญแจ teewang) ตอนตั้งอยู่ที่ %LOCALAPPDATA%\\TeewangBambu\\bambu.json ของคนที่ตั้ง
ฝังลงเครื่องแล้วก๊อปไป %PROGRAMDATA%\\TeewangBambu (เปิดอ่านได้เฉพาะ SYSTEM กับผู้ดูแล) task ชื่อ TeewangBambu รันเป็น SYSTEM ตอนเปิดเครื่อง
ก๊อป exe ไปให้คนอื่นแล้วรหัสไม่ติดไปด้วย
build: ./build.sh ในรีโปสาธารณะ github.com/Tayakorn000/teewang-bambu (Docker + Wine บน winserver)
ponytail: ยังไม่ได้ลองส่วน install บน Windows จริง เครื่องหลับ = หยุดส่ง (ถามตอน install ว่าให้ตั้งไม่หลับตอนเสียบปลั๊กไหม)
"""

import getpass
import json
import os
import shutil
import subprocess
import sys
import time
import traceback

import bambu
import bambu_probe
import bambu_setup

HOME = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TeewangBambu")
CFG = os.path.join(HOME, "bambu.json")
SVC = os.path.join(os.environ.get("PROGRAMDATA") or "C:\\ProgramData", "TeewangBambu")
SVC_EXE, SVC_CFG, LOG = os.path.join(SVC, "TeewangBambu.exe"), os.path.join(SVC, "bambu.json"), os.path.join(SVC, "log.txt")
TASK = "TeewangBambu"
STARTUP = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "TeewangBambu.cmd")
WIN = os.name == "nt"


def sh(*cmd):
    """คำสั่ง Windows คืน (สำเร็จไหม, ข้อความ) ไม่ throw"""
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="mbcs" if WIN else None, errors="replace")
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def admin():
    import ctypes

    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def elevate(arg):
    """เปิดตัวเองใหม่แบบผู้ดูแล (Windows ขึ้นถามยืนยัน) คืน True ถ้าเปิดได้"""
    import ctypes

    return ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, arg, None, 1) > 32


_mutex = None


def running():
    """มีตัวส่งตัวอื่นทำงานอยู่ในเครื่องนี้ไหม (กันส่งซ้อนกันสองตัว เครื่องปริ้นรับการต่อในวงได้จำกัด) ถือ mutex ไว้จนปิดโปรแกรม"""
    global _mutex
    if not WIN:
        return False
    import ctypes

    k = ctypes.windll.kernel32
    _mutex = k.CreateMutexW(None, False, "Global\\TeewangBambu")
    return k.GetLastError() in (183, 5)  # มีอยู่แล้ว / ของ SYSTEM ที่เราเปิดไม่ได้ = มีตัวอื่นถืออยู่


def installed():
    return WIN and sh("schtasks", "/query", "/tn", TASK)[0]


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


def install():
    """ก๊อป exe กับค่าตั้งไป ProgramData ล็อกให้อ่านได้เฉพาะ SYSTEM กับผู้ดูแล สร้าง task ตอนเปิดเครื่องแล้วเริ่มเลย"""
    if not os.path.exists(CFG):
        setup()
    sh("schtasks", "/end", "/tn", TASK)
    time.sleep(2)
    os.makedirs(SVC, exist_ok=True)
    if os.path.abspath(sys.executable).lower() != SVC_EXE.lower():
        shutil.copy2(sys.executable, SVC_EXE)
    shutil.copy2(CFG, SVC_CFG)
    ok, out = sh("icacls", SVC, "/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "/T")
    if not ok:
        print(f"ล็อกสิทธิ์โฟลเดอร์ไม่ได้: {out}")
    ok, out = sh("schtasks", "/create", "/tn", TASK, "/tr", f'"{SVC_EXE}" run', "/sc", "onstart", "/ru", "SYSTEM", "/rl", "HIGHEST", "/f")
    if not ok:
        sys.exit(f"สร้าง task ไม่ได้: {out}")
    if os.path.exists(STARTUP):
        os.remove(STARTUP)  # ของรุ่นเก่าที่เปิดหน้าต่างตอนล็อกอิน
    sh("schtasks", "/run", "/tn", TASK)
    print(f"ฝังลงเครื่องแล้ว ทำงานเบื้องหลังตั้งแต่เปิดเครื่อง ไม่ต้องล็อกอิน ปิดหน้าต่างนี้ได้เลย\nดูว่าทำงานไหม: เปิด TeewangBambu.exe อีกครั้ง หรือดู {LOG}")
    if input("ตั้งไม่ให้เครื่องหลับ และพับจอแล้วไม่หลับ ตอนเสียบปลั๊ก (เครื่องหลับ = หยุดส่ง) [Y/n]: ").strip().lower() != "n":
        for cmd in (("powercfg", "/change", "standby-timeout-ac", "0"), ("powercfg", "/change", "hibernate-timeout-ac", "0"),
                    ("powercfg", "/setacvalueindex", "SCHEME_CURRENT", "SUB_BUTTONS", "LIDACTION", "0"), ("powercfg", "/setactive", "SCHEME_CURRENT")):
            sh(*cmd)
        print("ตั้งแล้ว ใช้แบตอยู่ยังหลับตามปกติ")


def uninstall():
    sh("schtasks", "/end", "/tn", TASK)
    ok, out = sh("schtasks", "/delete", "/tn", TASK, "/f")
    print("เอา task ออกแล้ว" if ok else f"ไม่มี task หรือเอาออกไม่ได้: {out}")
    time.sleep(2)
    shutil.rmtree(SVC, ignore_errors=True)


def status():
    print("TeewangBambu ฝังอยู่ในเครื่องแล้ว ทำงานเบื้องหลัง" if running() else "TeewangBambu ฝังอยู่ในเครื่อง แต่ตอนนี้ไม่ได้ทำงาน (รีสตาร์ทเครื่อง หรือ TeewangBambu.exe install อีกครั้ง)")
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-12:]
        print("log ล่าสุด:\n" + "".join(tail))
    except OSError:
        print("ยังไม่มี log (หรือเปิดอ่านต้องใช้สิทธิ์ผู้ดูแล)")


def run():
    """ส่งไปเรื่อยๆ ไม่มีหน้าจอ เขียน log (เกิน 1 MB เริ่มไฟล์ใหม่) พังแล้วเริ่มใหม่ใน 30 วิ"""
    if os.path.exists(LOG) and os.path.getsize(LOG) > 1_000_000:
        os.replace(LOG, LOG + ".old")
    sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)
    if running():
        print(time.strftime("%Y-%m-%d %H:%M"), "มีตัวส่งทำงานอยู่แล้ว ออก")
        return
    while True:
        try:
            with open(SVC_CFG, encoding="utf-8") as f:
                cfg = json.load(f)
            os.environ["TEEWANG_KEY"] = cfg.get("teewang_key", "")
            print(time.strftime("%Y-%m-%d %H:%M"), f"เริ่มส่ง {len(cfg['printers'])} เครื่อง")
            bambu.main(SVC_CFG)
        except Exception:  # noqa: BLE001 เน็ตหลุด ไฟล์เสีย อะไรก็ตาม ต้องกลับมาส่งต่อเอง
            traceback.print_exc()
        except SystemExit as e:
            print(time.strftime("%Y-%m-%d %H:%M"), f"หยุด: {e.code}")
        time.sleep(30)


def foreground():
    with open(CFG, encoding="utf-8") as f:
        cfg = json.load(f)
    os.environ["TEEWANG_KEY"] = cfg.get("teewang_key", "")
    print(f"เริ่มส่งสถานะ {len(cfg['printers'])} เครื่องเข้า teewang ทุก {bambu.PUSH_S} วินาที ปิดหน้าต่างนี้ = หยุดส่ง", flush=True)
    bambu.main(CFG)


def main():
    args = sys.argv[1:]
    if "run" in args:
        return run()
    if WIN:
        import ctypes

        k = ctypes.windll.kernel32
        k.SetConsoleOutputCP(65001)
        # ปิด QuickEdit: คลิกในหน้าต่างแล้ว Windows หยุดโปรแกรมไว้ตอนพิมพ์ข้อความถัดไป ตัวส่งค้างเงียบจนกว่าจะกด Esc
        mode = ctypes.c_uint32()
        h = k.GetStdHandle(-10)
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, (mode.value & ~0x40) | 0x80)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    if "--check" in args:
        import paho.mqtt.client  # noqa: F401

        print("ok")
        return
    if "probe" in args:
        bambu_probe.main(SVC_CFG if not os.path.exists(CFG) and os.path.exists(SVC_CFG) else CFG)
        input("เสร็จแล้ว แคปข้อความข้างบนส่งผู้ดูแลเว็บ กด Enter เพื่อปิด")
        return
    for cmd, fn in (("install", install), ("uninstall", uninstall)):
        if cmd in args:
            if WIN and not admin():
                print("ต้องใช้สิทธิ์ผู้ดูแล กด Yes ที่หน้าต่างที่ Windows ถาม" if elevate(cmd) else "เปิดแบบผู้ดูแลไม่ได้")
                return
            fn()
            input("กด Enter เพื่อปิด")
            return
    if "setup" in args:
        setup()
        if installed():
            print("ฝังลงเครื่องไว้แล้ว ต้องติดตั้งซ้ำเพื่อใช้ค่าใหม่")
            print("กด Yes ที่หน้าต่างที่ Windows ถาม" if elevate("install") else "เปิดแบบผู้ดูแลไม่ได้ รัน TeewangBambu.exe install เอง")
            return
    elif installed():
        status()
        input("กด Enter เพื่อปิด (ตั้งค่าใหม่: TeewangBambu.exe setup)")
        return
    elif not os.path.exists(CFG):
        setup()
    if WIN and not installed() and input("ฝังลงเครื่องเลยไหม ทำงานเบื้องหลังตั้งแต่เปิดเครื่อง ปิดหน้าต่างได้ ไม่ต้องล็อกอิน [Y/n]: ").strip().lower() != "n":
        print("กด Yes ที่หน้าต่างที่ Windows ถาม" if elevate("install") else "เปิดแบบผู้ดูแลไม่ได้ รัน TeewangBambu.exe install เอง")
        return
    if running():
        print("มีตัวส่งทำงานอยู่แล้วในเครื่องนี้")
        return
    foreground()


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
    except Exception:  # noqa: BLE001
        # เต็มๆ พร้อมชื่อไฟล์และบรรทัด ให้แคปส่งผู้ดูแลได้เลย ({e!r} ของ PermissionError ไม่บอกว่าไฟล์ไหน)
        traceback.print_exc()
        input("แคปข้อความข้างบนส่งผู้ดูแลเว็บ กด Enter เพื่อปิด")
