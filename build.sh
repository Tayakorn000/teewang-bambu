#!/bin/bash
# build TeewangBambu.exe บนเครื่อง Linux ที่มี Docker (ไม่ต้องมี Windows): PyInstaller ของ Windows รันใน Wine
#   ./build.sh        ได้ dist/TeewangBambu.exe
set -euo pipefail
cd "$(dirname "$0")"
docker run --rm -v "$PWD:/src" -w /src tobix/pywine:3.12 sh -c '
  wine python -m pip install -q "paho-mqtt==2.1.0" "pyinstaller==6.11.1" &&
  wine pyinstaller --noconfirm --noupx --onefile --console --name TeewangBambu bambu_app.py &&
  wine dist/TeewangBambu.exe --check'
ls -l dist/TeewangBambu.exe
