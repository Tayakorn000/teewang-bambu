#!/bin/bash
# คอมไพล์เฟิร์มแวร์ ESP ทั้ง 3 ชิป แล้ววางไฟล์ให้หน้าลงโปรแกรม (ESP Web Tools) ใน OUT
#   ./build.sh ~/Documents/kmutt-lib/public/esp
# ต้องมี arduino-cli, core esp32 3.3.x, ไลบรารี PubSubClient 2.8 กับ ArduinoJson 7
# แยกไฟล์ทีละส่วน ไม่ใช้ merged.bin: merged เขียนทับช่อง NVS ค่าตั้งจะหายทุกครั้งที่อัปเดต
set -euo pipefail
cd "$(dirname "$0")"
OUT=${1:?ใส่โฟลเดอร์ปลายทาง}
VERSION=1.0.0
python3 test/test_feed.py
TMP=$(mktemp -d)
CORE=$(ls -d ~/Library/Arduino15/packages/esp32/hardware/esp32/3.3.* | tail -1)
mkdir -p "$OUT"
cp "$CORE/tools/partitions/boot_app0.bin" "$OUT/"
builds=""
#   ชิป        fqbn                                  ตำแหน่ง bootloader
for row in "ESP32 esp32:esp32:esp32 4096" "ESP32-C3 esp32:esp32:esp32c3:CDCOnBoot=cdc 0" "ESP32-S3 esp32:esp32:esp32s3:CDCOnBoot=cdc 0"; do
  set -- $row
  name=$(echo "$1" | tr 'A-Z' 'a-z' | tr -d -)
  arduino-cli compile --fqbn "$2" --export-binaries --output-dir "$TMP/$name" TeewangBambuESP >/dev/null
  for part in bootloader partitions; do cp "$TMP/$name/TeewangBambuESP.ino.$part.bin" "$OUT/$name.$part.bin"; done
  cp "$TMP/$name/TeewangBambuESP.ino.bin" "$OUT/$name.app.bin"
  builds+="${builds:+,}
    {\"chipFamily\": \"$1\", \"parts\": [{\"path\": \"$name.bootloader.bin\", \"offset\": $3}, {\"path\": \"$name.partitions.bin\", \"offset\": 32768}, {\"path\": \"boot_app0.bin\", \"offset\": 57344}, {\"path\": \"$name.app.bin\", \"offset\": 65536}]}"
done
cat > "$OUT/manifest.json" <<JSON
{
  "name": "TeewangBambu ESP",
  "version": "$VERSION",
  "new_install_prompt_erase": true,
  "builds": [$builds
  ]
}
JSON
rm -rf "$TMP"
ls -l "$OUT"
