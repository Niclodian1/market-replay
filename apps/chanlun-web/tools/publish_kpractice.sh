#!/bin/sh
# Copy the phone PWA + rebuilt OHLCV into the standalone publish repo (~/kpractice).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${KPRACTICE_DIR:-$HOME/kpractice}"

if [ ! -d "$DEST/.git" ]; then
  echo "找不到发布仓库: $DEST"
  echo "请先按 README 建好独立仓库 kpractice。"
  exit 1
fi

python3 "$ROOT/tools/build_mobile_data.py"

rsync -a --delete \
  --exclude '.git/' \
  --exclude '.gitignore' \
  --exclude 'README.md' \
  --exclude '.nojekyll' \
  --exclude '.github/' \
  --exclude 'data/.gitignore' \
  "$ROOT/mobile/" "$DEST/"

# publish repo must ship JSON; drop the ignore used in the web repo
rm -f "$DEST/data/.gitignore"

echo "已同步到 $DEST"
echo "接下来:"
echo "  cd $DEST"
echo "  git add -A && git status"
echo "  git commit -m \"更新练习数据\" && git push"
