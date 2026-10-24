"""feed.h (ESP) ต้องให้ผลเหมือน item() ใน bambu.py ทุกเคส: คอมไพล์ด้วย c++ บน Mac แล้วเทียบ

  python3 esp32/test/test_feed.py
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
import bambu  # noqa: E402

AJ = os.path.expanduser("~/Documents/Arduino/libraries/ArduinoJson/src")

FULL = {"gcode_state": "RUNNING", "mc_percent": 29, "mc_remaining_time": 346, "nozzle_temper": 219.6, "bed_temper": "55.0", "chamber_temper": 31,
        "layer_num": 120, "total_layer_num": 410, "subtask_name": "  gear box v2  ", "print_error": 0x05004038,
        "hms": [{"attr": 0x03000100, "code": 0x00010007}],
        "ams": {"tray_now": "5", "ams": [{"id": "0", "tray": [{"id": "0", "tray_type": "PLA", "tray_color": "00AE42FF", "remain": 80}, {"id": "1"}]},
                                         {"id": "1", "tray": [{"id": "1", "tray_type": "PETG", "tray_color": "zz", "remain": -1}]}]},
        "vt_tray": {"tray_type": "TPU", "tray_color": "FFFFFFFF", "remain": 0}}
CASES = [
    ("IX-3D-X1E-09", "X1E", FULL, 10, 10, 10, True),
    ("IX-3D-X1E-09", "X1E", FULL, 10, 10, 10 + 361, True),  # ไม่มีรายงานนานเกิน = ออฟไลน์
    ("IX-3D-X1E-09", "X1E", FULL, 10, 10, 20, False),  # ต่อไม่ได้
    ("IX-3D-01", "", {}, 0, 0, 0, True),  # ยังไม่เคยได้รายงาน
    ("IX-3D-01", None, {"gcode_state": "FINISH", "mc_percent": 100, "mc_remaining_time": 0}, 100, 100, 100 + 60, True),
    ("IX-3D-01", None, {"gcode_state": "FINISH"}, 2000, 100, 100 + bambu.DONE_AS_IDLE_S + 1, True),  # ค้างเสร็จนานเกิน = ว่าง
    ("IX-3D-01", None, {"gcode_state": "FAILED", "print_error": 0}, 5, 5, 5, True),
    ("IX-3D-01", None, {"gcode_state": "PAUSE", "mc_percent": 101, "mc_remaining_time": 99999}, 5, 5, 5, True),
    ("IX-3D-01", None, {"gcode_state": "SOMETHING_NEW"}, 5, 5, 5, True),
    ("IX-3D-01", None, {"gcode_state": "IDLE", "ams": {"tray_now": "255"}, "nozzle_temper": 9999, "bed_temper": None, "chamber_temper": "abc", "layer_num": 2.5, "total_layer_num": 3.5}, 5, 5, 5, True),
    ("IX-3D-01", None, {"gcode_state": "IDLE", "ams": {"tray_now": "254", "ams": [{"id": "4", "tray": [{"id": "0", "tray_type": "PLA"}]}, {"id": 3, "tray": [{"id": 3, "tray_type": " ABS-GF ทดสอบ  ", "tray_color": "ff00ff", "remain": 100}, {"id": "9", "tray_type": "PLA"}]}]}}, 5, 5, 5, True),
    ("IX-3D-01", None, {"gcode_state": "RUNNING", "subtask_name": "ชิ้นงาน" * 20, "hms": [{"attr": i, "code": i} for i in range(1, 12)], "print_error": 7}, 5, 5, 5, True),
    ("IX-3D-01", None, {"gcode_state": "RUNNING", "subtask_name": "   ", "vt_tray": {"tray_type": "", "tray_color": "FFFFFF"}}, 5, 5, 5, True),
]


def main():
    exe = os.path.join(tempfile.mkdtemp(), "feed")
    subprocess.run(["c++", "-std=c++17", "-Wall", "-Werror", f"-I{AJ}", os.path.join(HERE, "feed_main.cpp"), "-o", exe], check=True)
    cases = [{"id": i, "model": m, "print": pr, "seen": seen, "since": since, "now": now, "connected": conn} for i, m, pr, seen, since, now, conn in CASES]
    got = json.loads(subprocess.run([exe], input=json.dumps({"cases": cases}), capture_output=True, text=True, check=True).stdout)["items"]
    for c, g in zip(CASES, got):
        i, m, pr, seen, since, now, conn = c
        p = bambu.merge({}, {"print": pr}, seen)
        p["since"] = since
        want = bambu.item(i, p, now, conn, m)
        assert g == want, f"\n case {c}\n esp    {g}\n python {want}"
    print(f"feed.h ตรงกับ bambu.py {len(CASES)} เคส")


if __name__ == "__main__":
    main()
