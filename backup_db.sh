#!/bin/bash
set -euo pipefail

# cronは環境変数をほとんど引き継がないので、明示的に指定する
export PATH=/usr/local/bin:/usr/bin:/bin
export AWS_DEFAULT_REGION=ap-northeast-1

# リポジトリを公開する場合は、このバケット名を.envに移すこと(アカウントIDを含むため)
BUCKET="s3://splive-db-backup-aoki-886104576984-ap-northeast-1-an"
DB="/home/ubuntu/spltube/db.sqlite3"
NAME="splive-$(date -u +%Y%m%d-%H%M%S).sqlite3"
TMP="/tmp/$NAME"

# 単純なcpだと書き込み中のファイルをコピーして壊れることがあるので、
# SQLite自身にロックを取らせてコピーさせる
sqlite3 "$DB" ".backup '$TMP'"

# コピーが壊れていないか検証する(壊れていたらここで止まり、S3には送らない)
sqlite3 "$TMP" "PRAGMA integrity_check;" | grep -q "^ok$"

aws s3 cp "$TMP" "$BUCKET/$NAME"
rm -f "$TMP"

echo "$(date -u '+%Y-%m-%d %H:%M:%S') UTC backup ok: $NAME"
