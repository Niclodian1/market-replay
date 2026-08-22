#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  V1 告警体系快照工具（告警体系 V2 · 里程碑 0）
#
#  用途:
#    create <dest>  创建一份可校验的 V1 快照（源码 + 告警六表 + 渠道配置）
#    verify <dir>   校验快照完整性（文件存在 / SHA-256 / pg_restore 清单）
#
#  快照内容:
#    - source.tgz       应用源码（backend/app、backend/tests、frontend 相关目录、
#                       Dockerfile、requirements/package/lock、docker-compose.yml、
#                       .env.example）；不含 .env、行情数据、缓存
#    - alerts.pgdump    users/watchlists/alerts/notification_channels/monitor_plans/
#                       alert_events 六表，PostgreSQL custom format
#    - row_counts.tsv   六表行数（快照时刻）
#    - metadata.json    备份元信息（HEAD / git 摘要 / 工具版本 / 行数）
#    - MANIFEST.sha256  全部文件的 SHA-256 清单
#
#  安全约定:
#    - 备份目录权限 700，含渠道信息文件 600
#    - 任何日志不得打印 webhook / token（绝不 cat pgdump 或渠道配置）
#    - 快照位于 reports/alert-backups/，已被 .gitignore 忽略，不得提交
#
#  依赖: docker（postgres 容器）、/usr/bin/tar、/usr/bin/shasum
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_ROOT="$REPO_ROOT/reports/alert-backups"
PG_CONTAINER="${ALERT_PG_CONTAINER:-market-replay-postgres-1}"
PG_USER="${POSTGRES_USER:-marketuser}"
PG_DB="${POSTGRES_DB:-marketreplay}"
TAR=/usr/bin/tar
SHASUM=/usr/bin/shasum
ALERT_TABLES="users watchlists alerts notification_channels monitor_plans alert_events"

log() { printf '[alert-backup] %s\n' "$*"; }
die() { printf '[alert-backup] 错误: %s\n' "$*" >&2; exit 1; }

_usage() {
  cat <<EOF
用法:
  alert_backup.sh create [<dest>]   创建 V1 快照（默认 reports/alert-backups/<UTC时间>）
  alert_backup.sh verify <dir>      校验快照（文件 / SHA-256 / pg_restore 清单）
EOF
}

# ── 源码打包列表（只打包白名单，绝不包含 .env / 行情数据 / 缓存）──────────────
_source_paths() {
  printf '%s\n' \
    backend/app backend/tests \
    frontend/app frontend/components frontend/lib frontend/types \
    backend/Dockerfile frontend/Dockerfile \
    backend/requirements.txt backend/requirements-dev.txt \
    frontend/package.json frontend/package-lock.json \
    docker-compose.yml .env.example
}

_exclude_args() {
  printf -- '--exclude=%s\n' \
    '__pycache__' '*.pyc' '*.pyo' '.pytest_cache' \
    'node_modules' '.next' '*.tsbuildinfo' \
    'data' '*.parquet' '*.csv' '.DS_Store'
}

# ── 从 postgres 容器读取六表行数（tab 分隔: 表名<TAB>行数）────────────────────
_db_row_counts() {
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
    "SELECT 'users'||E'\\t'||count(*) FROM users
     UNION ALL SELECT 'watchlists'||E'\\t'||count(*) FROM watchlists
     UNION ALL SELECT 'alerts'||E'\\t'||count(*) FROM alerts
     UNION ALL SELECT 'notification_channels'||E'\\t'||count(*) FROM notification_channels
     UNION ALL SELECT 'monitor_plans'||E'\\t'||count(*) FROM monitor_plans
     UNION ALL SELECT 'alert_events'||E'\\t'||count(*) FROM alert_events;"
}

_git_summary() {
  local head tracked deleted untracked
  head="$(cd "$REPO_ROOT" && git rev-parse HEAD 2>/dev/null || echo unknown)"
  tracked="$(cd "$REPO_ROOT" && git status --short 2>/dev/null | grep -vc '^??' || true)"
  deleted="$(cd "$REPO_ROOT" && git status --short 2>/dev/null | grep -cE '^( D|D )' || true)"
  untracked="$(cd "$REPO_ROOT" && git status --short 2>/dev/null | grep -c '^??' || true)"
  printf '{"head":"%s","tracked_changed":%s,"deleted":%s,"untracked":%s}' \
    "$head" "${tracked:-0}" "${deleted:-0}" "${untracked:-0}"
}

_write_metadata() {
  local dest="$1" counts="$2" tool_bash tool_docker pg_version
  tool_bash="$(bash --version | head -1)"
  tool_docker="$(docker --version 2>/dev/null || echo n/a)"
  pg_version="$(docker exec "$PG_CONTAINER" pg_dump --version 2>/dev/null || echo n/a)"
  {
    printf '{\n'
    printf '  "backup_id": "%s",\n' "$(basename "$dest")"
    printf '  "created_at_utc": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '  "repo_head": %s,\n' "$(_git_summary)"
    printf '  "tools": {\n'
    printf '    "tar": "%s",\n' "$($TAR --version | head -1)"
    printf '    "shasum": "%s",\n' "$($SHASUM --version 2>/dev/null | head -1 || echo /usr/bin/shasum)"
    printf '    "bash": "%s",\n' "$tool_bash"
    printf '    "docker": "%s",\n' "$tool_docker"
    printf '    "pg_dump": "%s"\n' "$pg_version"
    printf '  },\n'
    printf '  "tables": [\n'
    local first=1 line
    while IFS=$'\t' read -r table count; do
      if [ "$first" -eq 1 ]; then first=0; else printf ',\n'; fi
      printf '    {"table":"%s","rows":%s}' "$table" "$count"
    done <<< "$counts"
    printf '\n  ]\n'
    printf '}\n'
  } > "$dest/metadata.json"
}

# ── create ──────────────────────────────────────────────────────────────────────
cmd_create() {
  local dest="${1:-$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ)}"
  if [ -e "$dest" ]; then
    die "目标已存在: $dest"
  fi
  mkdir -p "$dest"
  chmod 700 "$dest"

  log "创建快照: $dest"

  # 1) 源码快照（排除缓存/密钥/行情数据）
  log "打包源码 source.tgz ..."
  $TAR -C "$REPO_ROOT" -czf "$dest/source.tgz" \
    $(_exclude_args) $(_source_paths)
  [ -s "$dest/source.tgz" ] || die "source.tgz 为空"

  # 2) 告警数据库六表（custom format）
  log "导出告警六表 alerts.pgdump ..."
  docker exec "$PG_CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" -Fc \
    -t users -t watchlists -t alerts \
    -t notification_channels -t monitor_plans -t alert_events \
    > "$dest/alerts.pgdump" 2>/dev/null
  [ -s "$dest/alerts.pgdump" ] || die "alerts.pgdump 为空"

  # 3) 行数快照
  log "记录六表行数 row_counts.tsv ..."
  _db_row_counts > "$dest/row_counts.tsv"
  [ -s "$dest/row_counts.tsv" ] || die "row_counts.tsv 为空"

  # 4) 元信息
  log "写入 metadata.json ..."
  _write_metadata "$dest" "$(cat "$dest/row_counts.tsv")"

  # 5) 权限收紧：目录 700，含渠道信息文件 600
  chmod 700 "$dest"
  chmod 600 "$dest/source.tgz" "$dest/alerts.pgdump" \
    "$dest/row_counts.tsv" "$dest/metadata.json"

  # 6) SHA-256 清单
  log "生成 MANIFEST.sha256 ..."
  (cd "$dest" && $SHASUM -a 256 source.tgz alerts.pgdump row_counts.tsv metadata.json > MANIFEST.sha256)
  chmod 600 "$dest/MANIFEST.sha256"

  log "完成。文件列表:"
  ls -la "$dest" | sed 's/^/[alert-backup] /'
  log "六表行数:"
  cat "$dest/row_counts.tsv" | sed 's/^/[alert-backup]   /'
}

# ── verify ──────────────────────────────────────────────────────────────────────
cmd_verify() {
  local dir="${1:-}"
  [ -n "$dir" ] || die "用法: alert_backup.sh verify <dir>"
  [ -d "$dir" ] || die "快照目录不存在: $dir"

  log "校验快照: $dir"

  # 1) 必需文件存在
  local f
  for f in source.tgz alerts.pgdump row_counts.tsv metadata.json MANIFEST.sha256; do
    [ -f "$dir/$f" ] || die "缺少文件: $f"
  done

  # 2) 权限检查（严格：不符即失败）
  local perm
  perm="$(stat -f '%Lp' "$dir")"
  [ "$perm" = "700" ] || die "目录权限错误: ${perm}（期望 700）"
  local f2
  for f2 in source.tgz alerts.pgdump row_counts.tsv metadata.json MANIFEST.sha256; do
    perm="$(stat -f '%Lp' "$dir/$f2")"
    [ "$perm" = "600" ] || die "$f2 权限错误: ${perm}（期望 600，含渠道信息）"
  done

  # 3) SHA-256 校验（任一文件被篡改即失败）
  log "校验 SHA-256 ..."
  (cd "$dir" && $SHASUM -a 256 -c MANIFEST.sha256) >/dev/null \
    || die "SHA-256 校验失败：快照被篡改或损坏"

  # 4) pg_restore 清单（custom format 可解析，且含六表）
  log "校验 pg_restore 清单 ..."
  local toc
  toc="$(docker exec -i "$PG_CONTAINER" pg_restore --list < "$dir/alerts.pgdump" 2>/dev/null || true)"
  if [ -z "$toc" ]; then
    die "alerts.pgdump 无法解析（pg_restore --list 为空）"
  fi
  for t in users watchlists alerts notification_channels monitor_plans alert_events; do
    printf '%s' "$toc" | grep -q "TABLE public $t " || die "pgdump 缺少表: $t"
  done

  # 5) metadata.json 的 backup_id 与目录名一致
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$dir" <<'PY' || die "metadata.json 的 backup_id 与目录名不一致"
import json, sys
meta = json.load(open(sys.argv[1] + "/metadata.json", encoding="utf-8"))
if meta.get("backup_id") != __import__("os").path.basename(__import__("os").path.normpath(sys.argv[1])):
    sys.exit(1)
PY
  fi

  # 6) metadata.json 与 row_counts.tsv 逐表行数一致
  if command -v python3 >/dev/null 2>&1; then
    log "校验 row_counts.tsv 与 metadata.json 逐表行数一致 ..."
    python3 - "$dir" <<'PY' || die "metadata.json 与 row_counts.tsv 行数不一致"
import json, sys
meta = json.load(open(sys.argv[1] + "/metadata.json", encoding="utf-8"))
counts = {}
for line in open(sys.argv[1] + "/row_counts.tsv", encoding="utf-8"):
    parts = line.rstrip("\n").split("\t")
    if len(parts) == 2:
        counts[parts[0]] = int(parts[1])
if len(counts) != 6:
    sys.exit(1)
for item in meta.get("tables", []):
    if counts.get(item["table"]) != item["rows"]:
        sys.exit(1)
sys.exit(0)
PY
  fi

  log "校验通过: 文件齐全 / 权限正确 / SHA-256 一致 / pg_restore 清单含六表 / 行数一致"
}

case "${1:-}" in
  create) shift; cmd_create "${1:-}" ;;
  verify) shift; cmd_verify "${1:-}" ;;
  *) _usage; exit 1 ;;
esac
