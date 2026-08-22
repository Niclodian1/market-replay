#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  V1 告警体系恢复/演练工具（告警体系 V2 · 里程碑 0）
#
#  用途:
#    drill <backup-dir>   恢复演练：源码解到 mktemp 临时目录、六表恢复到唯一
#                         命名临时数据库、逐表对比快照行数、成功后清理临时
#                         资源；绝不触碰现用数据库与服务。
#    restore <backup-dir> 生产回滚：必须给出 backup-id 并显式确认；先自动生成
#                         rescue backup（当前现场），再恢复源码 + 六表。
#
#  安全约定:
#    - 默认只读/拒绝生产覆盖；restore 需要显式确认（输入 backup-id）
#    - 任何日志不得打印 webhook / token
#    - 演练的临时数据库使用唯一命名（UTC 时间 + PID），结束即删
#    - 生产 restore 兼容真实 schema：备份外的表（如 replay_notes）若以
#      外键引用六表，先摘除外键、恢复后重建，避免 pg_restore --clean 失败
#    - 源码恢复为「同步」语义：覆盖白名单路径并删除快照中不存在的新增文件
#
#  本脚本可被 source（提供 _restore_db / _sync_source 供测试复用），
#  仅作为主脚本执行时才运行命令行入口。
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_ROOT="$REPO_ROOT/reports/alert-backups"
PG_CONTAINER="${ALERT_PG_CONTAINER:-market-replay-postgres-1}"
PG_USER="${POSTGRES_USER:-marketuser}"
PG_DB="${POSTGRES_DB:-marketreplay}"
BACKUP_SH="$REPO_ROOT/scripts/ops/alert_backup.sh"
ALERT_TABLES="users watchlists alerts notification_channels monitor_plans alert_events"
# 六表 regclass 列表（SQL 注入面为脚本内常量，安全）
ALERT_TABLES_SQL="'users'::regclass,'watchlists'::regclass,'alerts'::regclass,'notification_channels'::regclass,'monitor_plans'::regclass,'alert_events'::regclass"
# 快照 source.tgz 的白名单路径（目录 = 同步删除语义；文件 = 覆盖语义）
SOURCE_DIRS="backend/app backend/tests frontend/app frontend/components frontend/lib frontend/types"
SOURCE_FILES="backend/Dockerfile frontend/Dockerfile backend/requirements.txt backend/requirements-dev.txt frontend/package.json frontend/package-lock.json docker-compose.yml .env.example"

log() { printf '[alert-restore] %s\n' "$*"; }
die() { printf '[alert-restore] 错误: %s\n' "$*" >&2; exit 1; }

_usage() {
  cat <<EOF
用法:
  alert_restore.sh drill <backup-dir>    恢复演练（临时库，安全，可反复执行）
  alert_restore.sh restore <backup-dir>  生产回滚（需输入 backup-id 显式确认，
                                         先自动生成 rescue backup）
EOF
}

# ── 备份前置校验：目录存在且 verify 通过 ──────────────────────────────────────
_require_backup() {
  local dir="${1:-}"
  [ -n "$dir" ] || die "缺少 <backup-dir> 参数"
  [ -d "$dir" ] || die "备份目录不存在: $dir"
  [ -f "$dir/MANIFEST.sha256" ] || die "不是有效备份目录（缺少 MANIFEST.sha256）: $dir"
  log "校验备份完整性: $dir"
  "$BACKUP_SH" verify "$dir" >/dev/null || die "备份校验未通过，中止"
}

# ── 从 row_counts.tsv 读取指定表行数 ──────────────────────────────────────────
_table_rows() {
  local dir="$1" table="$2"
  awk -F'\t' -v t="$table" '$1 == t {print $2; found=1} END {exit !found}' "$dir/row_counts.tsv" \
    || die "row_counts.tsv 中找不到表 $table"
}

# ── 指定库六表行数（tab 分隔）─────────────────────────────────────────────────
_db_counts() {
  local db="$1"
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$db" -tAc \
    "SELECT 'users'||E'\\t'||count(*) FROM users
     UNION ALL SELECT 'watchlists'||E'\\t'||count(*) FROM watchlists
     UNION ALL SELECT 'alerts'||E'\\t'||count(*) FROM alerts
     UNION ALL SELECT 'notification_channels'||E'\\t'||count(*) FROM notification_channels
     UNION ALL SELECT 'monitor_plans'||E'\\t'||count(*) FROM monitor_plans
     UNION ALL SELECT 'alert_events'||E'\\t'||count(*) FROM alert_events;"
}

_live_counts() {
  _db_counts "$PG_DB"
}

# ── 数据恢复（可复用；restore 与测试共用）────────────────────────────────────
# 原子事务：摘除外键 → pg_restore 导入六表 → 重建外键，全部在单个 SQL 事务中。
# 任一环节失败（如重建外键时发现现场数据与快照不一致）→ 整个事务回滚，
# 现场自动复原，不会留下「已恢复一半」的破坏状态。
_restore_db() {
  local backup_dir="$1" db_name="$2"
  local fk_lines rel con def drop_sql="" rebuild_sql="" schema sql_file

  # 六表所在 schema（重建外键时用于恢复 search_path）
  schema="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$db_name" -tAc \
    "SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relname='users' LIMIT 1;" | tr -d '[:space:]')"
  if [ -z "$schema" ]; then
    log "无法确定 users 表所在 schema"
    return 1
  fi

  # 备份外引用六表的外键（schema 限定名），生成事务内摘除/重建 SQL
  fk_lines="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$db_name" -tAc "
    SELECT format('%I.%I', n.nspname, rel.relname) || '|' || c.conname || '|' || pg_get_constraintdef(c.oid)
    FROM pg_constraint c
    JOIN pg_class rel ON rel.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = rel.relnamespace
    WHERE c.contype = 'f'
      AND c.confrelid IN ($ALERT_TABLES_SQL)
      AND c.conrelid NOT IN ($ALERT_TABLES_SQL);")"
  if [ -n "$fk_lines" ]; then
    while IFS='|' read -r rel con def; do
      [ -n "$rel" ] || continue
      log "备份外外键（事务内摘除并重建）: $rel.$con"
      drop_sql+="ALTER TABLE $rel DROP CONSTRAINT $con;\n"
      rebuild_sql+="ALTER TABLE $rel ADD CONSTRAINT $con $def;\n"
    done <<< "$fk_lines"
  fi

  # pg_restore 输出 SQL 文本到临时文件（作为事务管道的一部分）
  sql_file="$(mktemp "${TMPDIR:-/tmp}/alert_restore.XXXXXX")"
  if ! docker exec -i "$PG_CONTAINER" pg_restore --clean --if-exists --no-owner --no-privileges \
      -f - < "$backup_dir/alerts.pgdump" > "$sql_file" 2>/dev/null; then
    rm -f "$sql_file"
    log "pg_restore 生成 SQL 失败"
    return 1
  fi

  log "原子恢复（单事务：摘除外键→导入六表→重建外键）: $db_name ..."
  if ! {
    printf 'BEGIN;\n'
    printf '%b' "$drop_sql"
    cat "$sql_file"
    # pg_restore 的 set_config 会清空 search_path，重建前恢复
    printf 'SET LOCAL search_path TO %s;\n' "$schema"
    printf '%b' "$rebuild_sql"
    printf 'COMMIT;\n'
  } | docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$db_name" -v ON_ERROR_STOP=1 -q >/dev/null 2>&1; then
    rm -f "$sql_file"
    log "原子恢复失败：事务已回滚，现场自动复原（未做任何破坏性修改）"
    return 1
  fi
  rm -f "$sql_file"
  return 0
}

# ── 源码恢复（同步语义：覆盖白名单路径并删除快照中不存在的新增文件）──────────
_sync_source() {
  local backup_dir="$1" repo_root="$2"
  local tmpdir d f
  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/alert_sync.XXXXXX")"
  tar -xzf "$backup_dir/source.tgz" -C "$tmpdir"
  for d in $SOURCE_DIRS; do
    mkdir -p "$repo_root/$d"
    rsync -a --delete "$tmpdir/$d/" "$repo_root/$d/"
  done
  for f in $SOURCE_FILES; do
    cp "$tmpdir/$f" "$repo_root/$f"
  done
  rm -rf "$tmpdir"
}

# ── drill：恢复演练 ─────────────────────────────────────────────────────────────
cmd_drill() {
  local backup_dir="${1:-}"
  _require_backup "$backup_dir"
  local tmpdir dbname before_live after_live
  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/alert_drill.XXXXXX")"
  dbname="alert_drill_$(date -u +%Y%m%dT%H%M%SZ)_$$"

  cleanup() {
    # 幂等清理：临时库 + 临时目录
    docker exec "$PG_CONTAINER" dropdb -U "$PG_USER" --if-exists "$dbname" >/dev/null 2>&1 || true
    rm -rf "$tmpdir"
  }
  trap cleanup EXIT

  log "演练开始: 临时源码目录=$tmpdir 临时数据库=$dbname"
  before_live="$(_live_counts)"
  log "现用库行数（演练前）:"
  printf '%s\n' "$before_live" | sed 's/^/[alert-restore]   /'

  # 1) 源码解压到 mktemp（不触碰工作区）
  log "解压 source.tgz 到临时目录 ..."
  tar -xzf "$backup_dir/source.tgz" -C "$tmpdir"
  [ -d "$tmpdir/backend/app" ] || die "source.tgz 解压后缺少 backend/app"

  # 2) 六表恢复到唯一命名临时数据库
  log "创建临时数据库 $dbname ..."
  docker exec "$PG_CONTAINER" createdb -U "$PG_USER" "$dbname"
  log "恢复 alerts.pgdump → $dbname ..."
  docker exec -i "$PG_CONTAINER" pg_restore --no-owner --no-privileges \
    -U "$PG_USER" -d "$dbname" < "$backup_dir/alerts.pgdump" >/dev/null 2>&1 \
    || { ok=0; die "pg_restore 失败"; }

  # 3) 逐表对比行数
  log "逐表对比行数（快照 vs 恢复库）..."
  local t expected actual
  for t in $ALERT_TABLES; do
    expected="$(_table_rows "$backup_dir" "$t")"
    actual="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$dbname" -tAc \
      "SELECT count(*) FROM $t;" | tr -d '[:space:]')"
    if [ "$expected" = "$actual" ]; then
      log "  OK  $t: $actual"
    else
      die "行数不一致: $t 快照=$expected 恢复库=$actual"
    fi
  done

  # 4) 校验现用库未被改动
  after_live="$(_live_counts)"
  if [ "$before_live" != "$after_live" ]; then
    die "现用数据库行数在演练前后发生变化！"
  fi
  log "现用库行数（演练后）不变:"
  printf '%s\n' "$after_live" | sed 's/^/[alert-restore]   /'

  log "演练成功: 六表行数一致 / 临时资源已清理 / 现用库未被触碰"
  trap - EXIT
  cleanup
}

# ── restore：生产回滚 ───────────────────────────────────────────────────────────
cmd_restore() {
  local backup_dir="${1:-}"
  _require_backup "$backup_dir"
  local backup_id
  backup_id="$(basename "$backup_dir")"

  log "⚠  生产回滚请求: $backup_dir"
  log "    将覆盖: 白名单源码路径（同步删除快照外新增文件）+ 告警六表"
  printf '确认继续？请输入 backup-id [%s] 或输入 abort 取消: ' "$backup_id"
  read -r answer
  if [ "$answer" != "$backup_id" ]; then
    die "确认失败（输入与 backup-id 不符），已取消，未做任何修改"
  fi

  # 1) 先自动生成 rescue backup（当前现场）
  local rescue_dir="$BACKUP_ROOT/rescue-$(date -u +%Y%m%dT%H%M%SZ)"
  log "生成 rescue backup: $rescue_dir ..."
  "$BACKUP_SH" create "$rescue_dir" >/dev/null || die "rescue backup 生成失败，中止回滚"
  log "rescue backup 完成: $rescue_dir"

  # 2) 恢复源码（同步语义）
  log "恢复源码（同步覆盖 + 删除快照外新增文件）..."
  _sync_source "$backup_dir" "$REPO_ROOT"
  log "源码已恢复"

  # 3) 恢复告警六表（原子事务：兼容真实 schema，失败自动回滚复原）
  log "恢复告警六表 ..."
  _restore_db "$backup_dir" "$PG_DB" \
    || die "原子恢复失败：事务已回滚，现场自动复原（rescue backup 仍保留: $rescue_dir）"

  # 4) 恢复后行数核对
  local t expected actual
  for t in $ALERT_TABLES; do
    expected="$(_table_rows "$backup_dir" "$t")"
    actual="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -tAc \
      "SELECT count(*) FROM $t;" | tr -d '[:space:]')"
    [ "$expected" = "$actual" ] || die "恢复后行数不一致: $t 期望=$expected 实际=${actual}（rescue: ${rescue_dir}）"
  done

  log "回滚完成。六表行数与快照一致；rescue backup: $rescue_dir"
  log "请重启 backend 服务使源码/缓存生效（docker compose restart backend）"
}

main() {
  case "${1:-}" in
    drill)   shift; cmd_drill "${1:-}" ;;
    restore) shift; cmd_restore "${1:-}" ;;
    *) _usage; exit 1 ;;
  esac
}

# 仅作为主脚本执行时进入命令行入口；被 source（测试复用）时不执行
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
