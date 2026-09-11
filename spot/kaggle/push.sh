#!/usr/bin/env bash
# Kaggle'a defteri gonderir, bitene kadar bekler, ciktilari indirir.
# Kullanim:  ./push.sh blender   |   ./push.sh broll
# On kosul: pip install kaggle  +  ~/.kaggle/kaggle.json (kaggle.com/settings -> Create New Token)
set -euo pipefail
cd "$(dirname "$0")/${1:-blender}"
USER_NAME=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.kaggle/kaggle.json')))['username'])")
sed -i "s#\"id\": \"KAGGLE_USERNAME/#\"id\": \"$USER_NAME/#" kernel-metadata.json
ID=$(python3 -c "import json;print(json.load(open('kernel-metadata.json'))['id'])")
kaggle kernels push -p .
echo "gonderildi: https://www.kaggle.com/code/$ID"
while :; do
  ST=$(kaggle kernels status "$ID" 2>&1 | tr -d '"')
  echo "$(date +%H:%M:%S) $ST"
  case "$ST" in *complete*|*error*|*cancel*) break;; esac
  sleep 60
done
mkdir -p ../../out && kaggle kernels output "$ID" -p ../../out
ls -la ../../out
