"""ส่งตัวเลขเข้า teewang (POST /api/feed) ตัวส่งทุกตัวใน agents/ ใช้ฟังก์ชันนี้ ใช้แค่ stdlib

กุญแจอ่านจาก env TEEWANG_KEY เสมอ ห้ามใส่ในไฟล์ config ที่อาจถูกก๊อปไปที่อื่น
"""

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "https://teewang.taokaetech.com/api/feed"


def push(items, url=None, key=None):
    """ส่งทั้งก้อน คืน True ถ้าเว็บรับ (204) ไม่ throw ตัวส่งต้องวนต่อได้แม้เน็ตหลุด"""
    key = key or os.environ.get("TEEWANG_KEY", "")
    if not key:
        sys.exit("ต้องตั้ง TEEWANG_KEY")
    req = urllib.request.Request(
        url or os.environ.get("TEEWANG_URL", DEFAULT_URL),
        data=json.dumps({"items": items}).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "teewang-agent/1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 204
    except urllib.error.HTTPError as e:
        print(f"push {e.code}: {e.read()[:200].decode(errors='replace')}", file=sys.stderr)
    except OSError as e:
        print(f"push failed: {e}", file=sys.stderr)
    return False
