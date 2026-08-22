#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  M3B 影子运行时 schema 工具
#
#  用途:
#    status   查看两表存在性/结构（默认活库；--db 可指定其他库）
#    apply    建 m3_shadow_runtime / m3_shadow_events（幂等，默认活库）
#    rollback 删除两张 M3 表（只删 M3 表；必须输入精确确认词
#             "ROLLBACK M3 SHADOW"；默认活库）
#    test     在唯一命名临时库验证：建表幂等 / 唯一键 / 无 V1 外键 /
#             回滚只删 M3 表（不触碰活库，结束即清理）
#
#  安全约定:
#    - 两表不对 V1 六表建任何外键；rollback 只删 M3 表
#    - 实施期间不得对现用库执行 rollback（任务书红线；test 只用于临时库）
#    - 日志不打印任何秘密
#
#  依赖: docker（postgres 容器）、psql
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PG_CONTAINER="${ALERT_PG_CONTAINER:-market-replay-postgres-1}"
PG_USER="${POSTGRES_USER:-marketuser}"
PG_DB="${POSTGRES_DB:-marketreplay}"

CONFIRM_PHRASE="ROLLBACK M3 SHADOW"
M3_TABLES="m3_shadow_runtime m3_shadow_events"
V1_TABLES="users watchlists alerts notification_channels monitor_plans alert_events"

log() { printf '[m3-shadow-schema] %s\n' "$*"; }
die() { printf '[m3-shadow-schema] 错误: %s\n' "$*" >&2; exit 1; }

_ddl() {
  cat <<'SQL'
CREATE TABLE IF NOT EXISTS m3_shadow_runtime (
    id          SMALLINT PRIMARY KEY CHECK (id = 1),
    started_at  BIGINT  NOT NULL,
    last_run_at BIGINT,
    last_run_ok BOOLEAN,
    last_error  TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS m3_shadow_events (
    id                 BIGSERIAL PRIMARY KEY,
    source_dedupe_key  VARCHAR(240) NOT NULL UNIQUE,
    source_event_id    BIGINT NOT NULL,
    symbol             VARCHAR(30) NOT NULL,
    timeframe          VARCHAR(10) NOT NULL,
    signal_time        BIGINT NOT NULL,
    source_snapshot    TEXT NOT NULL,
    decision_json      TEXT,
    decision_digest    VARCHAR(16),
    eligible           BOOLEAN,
    reasons            TEXT,
    config_hash        TEXT,
    status             VARCHAR(20) NOT NULL DEFAULT 'pending',
    failure_stage      VARCHAR(20),
    attempts           INTEGER NOT NULL DEFAULT 0,
    next_retry_at      BIGINT,
    last_error         TEXT,
    label_json         TEXT,
    label_as_of        BIGINT,
    maturity_window    INTEGER,
    next_label_at      BIGINT,
    created_at         TIMESTAMP NOT NULL DEFAULT now(),
    updated_at         TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT ck_m3_shadow_events_status
        CHECK (status IN ('pending','rejected','labeled','completed','failed')),
    CONSTRAINT ck_m3_shadow_events_timeframe CHECK (timeframe = '4h'),
    CONSTRAINT ck_m3_shadow_events_attempts CHECK (attempts >= 0),
    CONSTRAINT ck_m3_shadow_events_digest
        CHECK (decision_digest IS NULL OR decision_digest ~ '^[0-9a-f]{16}$'),
    CONSTRAINT ck_m3_shadow_events_maturity
        CHECK (COALESCE(maturity_window IS NULL, FALSE) OR maturity_window IN (5,10,20,40)),
    CONSTRAINT ck_m3_shadow_events_failure_stage
        CHECK (COALESCE(failure_stage IS NULL AND status <> 'failed', FALSE)
            OR COALESCE(failure_stage IN ('decision','label') AND status = 'failed', FALSE)),
    CONSTRAINT ck_m3_shadow_events_decision_complete
        CHECK ((failure_stage = 'decision'
                AND decision_json IS NULL AND decision_digest IS NULL
                AND eligible IS NULL AND reasons IS NULL AND config_hash IS NULL)
            OR (COALESCE(failure_stage <> 'decision', TRUE)
                AND decision_json IS NOT NULL AND decision_digest IS NOT NULL
                AND eligible IS NOT NULL AND reasons IS NOT NULL AND config_hash IS NOT NULL)),
    CONSTRAINT ck_m3_shadow_events_label_bound
        CHECK ((label_json IS NULL AND label_as_of IS NULL AND maturity_window IS NULL)
            OR (label_json IS NOT NULL AND label_as_of IS NOT NULL
                AND (maturity_window IS NOT NULL OR status = 'pending'))),
    CONSTRAINT ck_m3_shadow_events_state_consistency
        CHECK ((status = 'failed' AND failure_stage = 'decision' AND eligible IS NULL
                AND label_json IS NULL AND label_as_of IS NULL AND maturity_window IS NULL)
            OR (status = 'failed' AND failure_stage = 'label' AND eligible = TRUE
                AND decision_json IS NOT NULL AND decision_digest IS NOT NULL
                AND reasons IS NOT NULL AND config_hash IS NOT NULL)
            OR (status = 'rejected' AND eligible = FALSE
                AND label_json IS NULL AND label_as_of IS NULL AND maturity_window IS NULL)
            OR (status = 'pending' AND eligible = TRUE
                AND decision_json IS NOT NULL AND decision_digest IS NOT NULL
                AND reasons IS NOT NULL AND config_hash IS NOT NULL
                AND (label_json IS NULL
                     OR (label_as_of IS NOT NULL AND maturity_window IS NULL)))
            OR (status = 'labeled' AND eligible = TRUE
                AND decision_json IS NOT NULL AND decision_digest IS NOT NULL
                AND reasons IS NOT NULL AND config_hash IS NOT NULL
                AND label_json IS NOT NULL AND label_as_of IS NOT NULL
                AND maturity_window IS NOT NULL AND maturity_window < 40)
            OR (status = 'completed' AND eligible = TRUE
                AND decision_json IS NOT NULL AND decision_digest IS NOT NULL
                AND reasons IS NOT NULL AND config_hash IS NOT NULL
                AND label_json IS NOT NULL AND label_as_of IS NOT NULL
                AND maturity_window = 40)),
    CONSTRAINT ck_m3_shadow_events_retry_state
        CHECK ((status <> 'failed' AND attempts = 0 AND last_error IS NULL
                AND (next_retry_at IS NULL OR status IN ('pending','labeled')))
            OR (status = 'failed' AND attempts >= 1 AND next_retry_at IS NOT NULL
                AND last_error IS NOT NULL AND btrim(last_error) <> ''))
);

CREATE INDEX IF NOT EXISTS ix_m3_shadow_events_status ON m3_shadow_events (status);
CREATE INDEX IF NOT EXISTS ix_m3_shadow_events_symbol_signal ON m3_shadow_events (symbol, signal_time);
SQL
}

# 既有（旧结构）表迁移：加 failure_stage 列、decision 列改可空、补 CHECK 约束（幂等）
_migrate_ddl() {
  cat <<'SQL'
ALTER TABLE m3_shadow_events ADD COLUMN IF NOT EXISTS failure_stage VARCHAR(20);
ALTER TABLE m3_shadow_events ALTER COLUMN decision_json DROP NOT NULL;
ALTER TABLE m3_shadow_events ALTER COLUMN decision_digest DROP NOT NULL;
ALTER TABLE m3_shadow_events ALTER COLUMN eligible DROP NOT NULL;
ALTER TABLE m3_shadow_events ALTER COLUMN reasons DROP NOT NULL;
ALTER TABLE m3_shadow_events ALTER COLUMN config_hash DROP NOT NULL;
-- 与 ORM/DDL 对齐：DB 级默认值（后端 create_all 建的旧表可能只有 Python 侧默认）
ALTER TABLE m3_shadow_runtime ALTER COLUMN created_at SET DEFAULT now();
ALTER TABLE m3_shadow_runtime ALTER COLUMN updated_at SET DEFAULT now();
ALTER TABLE m3_shadow_events ALTER COLUMN status SET DEFAULT 'pending';
ALTER TABLE m3_shadow_events ALTER COLUMN attempts SET DEFAULT 0;
ALTER TABLE m3_shadow_events ALTER COLUMN created_at SET DEFAULT now();
ALTER TABLE m3_shadow_events ALTER COLUMN updated_at SET DEFAULT now();

DO $m3mig$
BEGIN
  ALTER TABLE m3_shadow_events DROP CONSTRAINT IF EXISTS ck_m3_shadow_events_status;
  ALTER TABLE m3_shadow_events ADD CONSTRAINT ck_m3_shadow_events_status
    CHECK (status IN ('pending','rejected','labeled','completed','failed'));
  ALTER TABLE m3_shadow_events DROP CONSTRAINT IF EXISTS ck_m3_shadow_events_timeframe;
  ALTER TABLE m3_shadow_events ADD CONSTRAINT ck_m3_shadow_events_timeframe CHECK (timeframe = '4h');
  ALTER TABLE m3_shadow_events DROP CONSTRAINT IF EXISTS ck_m3_shadow_events_attempts;
  ALTER TABLE m3_shadow_events ADD CONSTRAINT ck_m3_shadow_events_attempts CHECK (attempts >= 0);
  ALTER TABLE m3_shadow_events DROP CONSTRAINT IF EXISTS ck_m3_shadow_events_digest;
  ALTER TABLE m3_shadow_events ADD CONSTRAINT ck_m3_shadow_events_digest
    CHECK (decision_digest IS NULL OR decision_digest ~ '^[0-9a-f]{16}$');
  ALTER TABLE m3_shadow_events DROP CONSTRAINT IF EXISTS ck_m3_shadow_events_maturity;
  ALTER TABLE m3_shadow_events ADD CONSTRAINT ck_m3_shadow_events_maturity
    CHECK (COALESCE(maturity_window IS NULL, FALSE) OR maturity_window IN (5,10,20,40));
  ALTER TABLE m3_shadow_events DROP CONSTRAINT IF EXISTS ck_m3_shadow_events_failure_stage;
  ALTER TABLE m3_shadow_events ADD CONSTRAINT ck_m3_shadow_events_failure_stage
    CHECK (COALESCE(failure_stage IS NULL AND status <> 'failed', FALSE)
        OR COALESCE(failure_stage IN ('decision','label') AND status = 'failed', FALSE));
  ALTER TABLE m3_shadow_events DROP CONSTRAINT IF EXISTS ck_m3_shadow_events_decision_complete;
  ALTER TABLE m3_shadow_events ADD CONSTRAINT ck_m3_shadow_events_decision_complete
    CHECK ((failure_stage = 'decision'
            AND decision_json IS NULL AND decision_digest IS NULL
            AND eligible IS NULL AND reasons IS NULL AND config_hash IS NULL)
        OR (COALESCE(failure_stage <> 'decision', TRUE)
            AND decision_json IS NOT NULL AND decision_digest IS NOT NULL
            AND eligible IS NOT NULL AND reasons IS NOT NULL AND config_hash IS NOT NULL));
  ALTER TABLE m3_shadow_events DROP CONSTRAINT IF EXISTS ck_m3_shadow_events_label_bound;
  ALTER TABLE m3_shadow_events ADD CONSTRAINT ck_m3_shadow_events_label_bound
    CHECK ((label_json IS NULL AND label_as_of IS NULL AND maturity_window IS NULL)
        OR (label_json IS NOT NULL AND label_as_of IS NOT NULL
            AND (maturity_window IS NOT NULL OR status = 'pending')));
  ALTER TABLE m3_shadow_events DROP CONSTRAINT IF EXISTS ck_m3_shadow_events_state_consistency;
  ALTER TABLE m3_shadow_events ADD CONSTRAINT ck_m3_shadow_events_state_consistency
    CHECK ((status = 'failed' AND failure_stage = 'decision' AND eligible IS NULL
            AND label_json IS NULL AND label_as_of IS NULL AND maturity_window IS NULL)
        OR (status = 'failed' AND failure_stage = 'label' AND eligible = TRUE
            AND decision_json IS NOT NULL AND decision_digest IS NOT NULL
            AND reasons IS NOT NULL AND config_hash IS NOT NULL)
        OR (status = 'rejected' AND eligible = FALSE
            AND label_json IS NULL AND label_as_of IS NULL AND maturity_window IS NULL)
        OR (status = 'pending' AND eligible = TRUE
            AND decision_json IS NOT NULL AND decision_digest IS NOT NULL
            AND reasons IS NOT NULL AND config_hash IS NOT NULL
            AND (label_json IS NULL
                 OR (label_as_of IS NOT NULL AND maturity_window IS NULL)))
        OR (status = 'labeled' AND eligible = TRUE
            AND decision_json IS NOT NULL AND decision_digest IS NOT NULL
            AND reasons IS NOT NULL AND config_hash IS NOT NULL
            AND label_json IS NOT NULL AND label_as_of IS NOT NULL
            AND maturity_window IS NOT NULL AND maturity_window < 40)
        OR (status = 'completed' AND eligible = TRUE
            AND decision_json IS NOT NULL AND decision_digest IS NOT NULL
            AND reasons IS NOT NULL AND config_hash IS NOT NULL
            AND label_json IS NOT NULL AND label_as_of IS NOT NULL
            AND maturity_window = 40));
  ALTER TABLE m3_shadow_events DROP CONSTRAINT IF EXISTS ck_m3_shadow_events_retry_state;
  ALTER TABLE m3_shadow_events ADD CONSTRAINT ck_m3_shadow_events_retry_state
    CHECK ((status <> 'failed' AND attempts = 0 AND last_error IS NULL
            AND (next_retry_at IS NULL OR status IN ('pending','labeled')))
        OR (status = 'failed' AND attempts >= 1 AND next_retry_at IS NOT NULL
            AND last_error IS NOT NULL AND btrim(last_error) <> ''));
END $m3mig$;
SQL
}

_drop_ddl() {
  cat <<'SQL'
DROP TABLE IF EXISTS m3_shadow_events;
DROP TABLE IF EXISTS m3_shadow_runtime;
SQL
}

_usage() {
  cat <<EOF
用法:
  m3_shadow_schema.sh status [--db <db>]     查看两表状态
  m3_shadow_schema.sh apply  [--db <db>]     建两表（幂等）
  m3_shadow_schema.sh rollback [--db <db>]   删两表（需精确确认词 "$CONFIRM_PHRASE"）
  m3_shadow_schema.sh test                   临时库验证（不触碰活库）
EOF
}

_psql() {
  local db="$1"; shift
  docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$db" -v ON_ERROR_STOP=1 -q "$@"
}

cmd_status() {
  local db="$1"
  log "检查 $db 中的 M3 表 ..."
  local rows
  rows="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$db" -tAc \
    "SELECT table_name FROM information_schema.tables
     WHERE table_schema='public' AND table_name IN ('m3_shadow_runtime','m3_shadow_events')
     ORDER BY table_name;")"
  if [ -z "$rows" ]; then
    log "两表均不存在（M3B 尚未 apply）"
  else
    for t in $rows; do
      log "存在: $t"
      docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$db" -tAc \
        "SELECT '  列: '||string_agg(column_name, ', ' ORDER BY ordinal_position) FROM information_schema.columns WHERE table_name='$t' AND table_schema='public';" | sed 's/^/[m3-shadow-schema] /'
    done
  fi
  # 外键安全声明：M3 表不得引用 V1 六表
  local fk
  fk="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$db" -tAc \
    "SELECT count(*) FROM pg_constraint c
     JOIN pg_class rel ON rel.oid = c.conrelid
     JOIN pg_class ref ON ref.oid = c.confrelid
     WHERE c.contype='f' AND rel.relname IN ('m3_shadow_runtime','m3_shadow_events')
       AND ref.relname IN ($(printf "'%s'," $V1_TABLES | sed 's/,$//'));" | tr -d '[:space:]')"
  log "M3 表对 V1 六表外键数: ${fk:-0}（必须为 0）"
}

cmd_apply() {
  local db="$1"
  log "apply 到 ${db}（建表 + 迁移，显式单事务 BEGIN/COMMIT + ON_ERROR_STOP）..."
  _psql "$db" <<SQL
BEGIN;
$(_ddl)
$(_migrate_ddl)
COMMIT;
SQL
  log "完成。"
  cmd_status "$db"
}

cmd_rollback() {
  local db="$1"
  log "⚠  请求删除 M3 表（仅 ${M3_TABLES}）: ${db}"
  printf '确认继续？请输入精确确认词 [%s] 或输入 abort 取消: ' "$CONFIRM_PHRASE"
  read -r answer
  if [ "$answer" != "$CONFIRM_PHRASE" ]; then
    die "确认失败（输入与确认词不符），已取消，未做任何修改"
  fi
  _psql "$db" <<SQL
$(_drop_ddl)
SQL
  log "已删除两张 M3 表（V1 六表未触碰）。"
}

cmd_test() {
  # 全局（非 local）：trap 在函数返回后触发，cleanup 需仍能读取
  TEST_DB="m3_schema_test_$(date -u +%Y%m%dT%H%M%SZ)_$$"
  cleanup() {
    docker exec "$PG_CONTAINER" dropdb -U "$PG_USER" --if-exists "$TEST_DB" >/dev/null 2>&1 || true
  }
  trap cleanup EXIT

  log "临时库: $TEST_DB"
  docker exec "$PG_CONTAINER" createdb -U "$PG_USER" "$TEST_DB"

  # 预置 V1 六表空壳（验证「回滚只删 M3 表」不触碰 V1）
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -q \
    -c "CREATE TABLE users (id int PRIMARY KEY);" \
    -c "CREATE TABLE watchlists (id int PRIMARY KEY);" \
    -c "CREATE TABLE alerts (id int PRIMARY KEY);" \
    -c "CREATE TABLE notification_channels (id int PRIMARY KEY);" \
    -c "CREATE TABLE monitor_plans (id int PRIMARY KEY);" \
    -c "CREATE TABLE alert_events (id int PRIMARY KEY);"

  # 0) 原子回滚：整个 apply（建表+迁移）+ 故意失败 INSERT 必须在单事务内整体回滚
  log "原子回滚测试（apply 单事务 + 故意失败 → 整体回滚）..."
  set +e
  _psql "$TEST_DB" <<SQL >/dev/null 2>&1
BEGIN;
$(_ddl)
$(_migrate_ddl)
INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, decision_json, decision_digest, eligible, reasons, config_hash) VALUES ('dup1', 1, 'BINANCE:BTCUSDT', '4h', 1, '{}', '{}', '0123456789abcdef', true, '[]', '{}');
INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, decision_json, decision_digest, eligible, reasons, config_hash) VALUES ('dup1', 2, 'BINANCE:BTCUSDT', '4h', 2, '{}', '{}', '0123456789abcdef', true, '[]', '{}');
COMMIT;
SQL
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    die "原子回滚未生效：故意失败事务竟然成功"
  fi
  for t in $M3_TABLES; do
    if docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -tAc \
      "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='$t';" \
      | grep -q 1; then
      die "原子回滚失败：表 $t 残留（事务未整体回滚）"
    fi
  done
  log "PASS: apply（建表+迁移）显式单事务，故意失败整体回滚"

  # 1) apply
  log "第一次 apply（建表 + 迁移，单事务）..."
  _psql "$TEST_DB" <<SQL
BEGIN;
$(_ddl)
$(_migrate_ddl)
COMMIT;
SQL
  for t in $M3_TABLES; do
    docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -tAc \
      "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='$t';" \
      | grep -q 1 || die "表 $t 未创建"
  done
  log "PASS: 两表创建成功"

  # 2) 幂等：再次 apply 不报错、结构不变（含迁移）
  log "第二次 apply（幂等）..."
  _psql "$TEST_DB" <<SQL
BEGIN;
$(_ddl)
$(_migrate_ddl)
COMMIT;
SQL
  log "PASS: 建表/迁移幂等"

  # 3) 唯一键：同 source_dedupe_key 第二行必须失败
  log "唯一键测试 ..."
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -q \
    -c "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, decision_json, decision_digest, eligible, reasons, config_hash) VALUES ('k1', 1, 'BINANCE:BTCUSDT', '4h', 100, '{}', '{}', '0123456789abcdef', true, '[]', '{}');" \
    || die "首行插入失败"
  if docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -q \
    -c "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, decision_json, decision_digest, eligible, reasons, config_hash) VALUES ('k1', 2, 'BINANCE:BTCUSDT', '4h', 200, '{}', '{}', '0123456789abcdef', true, '[]', '{}');" >/dev/null 2>&1; then
    die "唯一键未生效：同 source_dedupe_key 第二行插入成功"
  fi
  log "PASS: source_dedupe_key 唯一键生效"

  # 4) 无 V1 外键：M3 表不得引用 V1 六表
  fk="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -tAc \
    "SELECT count(*) FROM pg_constraint c
     JOIN pg_class rel ON rel.oid = c.conrelid
     JOIN pg_class ref ON ref.oid = c.confrelid
     WHERE c.contype='f' AND rel.relname IN ('m3_shadow_runtime','m3_shadow_events')
       AND ref.relname IN ($(printf "'%s'," $V1_TABLES | sed 's/,$//'));" | tr -d '[:space:]')"
  [ "$fk" = "0" ] || die "M3 表存在对 V1 六表的外键: $fk"
  log "PASS: 无 V1 外键"

  # 5) 心跳单例 CHECK：id 必须为 1
  if docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -q \
    -c "INSERT INTO m3_shadow_runtime(id, started_at) VALUES (2, 1);" >/dev/null 2>&1; then
    die "心跳单例 CHECK 未生效（id=2 插入成功）"
  fi
  log "PASS: 心跳单例 CHECK(id=1) 生效"

  # 5b) 非法行必拒绝：status / timeframe / attempts / digest / maturity / failure_stage
  log "CHECK 约束测试（非法行必拒绝）..."
  _bad_insert() {
    docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -q -c "$1" >/dev/null 2>&1
  }
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status) VALUES ('b1', 1, 'S', '1h', 1, '{}', 'pending');" \
    && die "CHECK 未拒绝非法 timeframe"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, attempts) VALUES ('b2', 1, 'S', '4h', 1, '{}', 'pending', -1);" \
    && die "CHECK 未拒绝负数 attempts"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash) VALUES ('b3', 1, 'S', '4h', 1, '{}', 'weird', '{}', '0123456789abcdef', true, '[]', '{}');" \
    && die "CHECK 未拒绝非法 status"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash) VALUES ('b4', 1, 'S', '4h', 1, '{}', 'pending', '{}', 'ZZ', true, '[]', '{}');" \
    && die "CHECK 未拒绝非法 digest"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash, label_json, label_as_of, maturity_window) VALUES ('b5', 1, 'S', '4h', 1, '{}', 'labeled', '{}', '0123456789abcdef', true, '[]', '{}', '{}', 100, 9);" \
    && die "CHECK 未拒绝非法 maturity_window"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash) VALUES ('b6', 1, 'S', '4h', 1, '{}', 'pending', NULL, NULL, NULL, NULL, NULL);" \
    && die "CHECK 未拒绝空 decision 的非失败态"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, failure_stage) VALUES ('b7', 1, 'S', '4h', 1, '{}', 'pending', 'decision');" \
    && die "CHECK 未拒绝非 failed 带 failure_stage"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, failure_stage) VALUES ('b8', 1, 'S', '4h', 1, '{}', 'failed', 'nope');" \
    && die "CHECK 未拒绝非法 failure_stage"
  # 5b2) label_bound：pending censored 合法；maturity 无 label / label 无 as_of 拒绝
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -q \
    -c "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash, label_json, label_as_of, maturity_window) VALUES ('ok2', 1, 'BINANCE:BTCUSDT', '4h', 100, '{}', 'pending', '{}', '0123456789abcdef', true, '[]', '{}', '{}', 100, NULL);" \
    || die "pending censored（label_json+label_as_of、maturity NULL）插入失败"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash, label_json, label_as_of, maturity_window) VALUES ('b9', 1, 'S', '4h', 1, '{}', 'labeled', '{}', '0123456789abcdef', true, '[]', '{}', '{}', 100, NULL);" \
    && die "CHECK 未拒绝 labeled 无成熟 maturity"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash, label_json, label_as_of, maturity_window) VALUES ('b10', 1, 'S', '4h', 1, '{}', 'labeled', '{}', '0123456789abcdef', true, '[]', '{}', NULL, 100, 5);" \
    && die "CHECK 未拒绝 maturity 非空却无 label_json"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash, label_json, label_as_of, maturity_window) VALUES ('b11', 1, 'S', '4h', 1, '{}', 'pending', '{}', '0123456789abcdef', true, '[]', '{}', '{}', NULL, NULL);" \
    && die "CHECK 未拒绝 label_json 非空却无 label_as_of"
  # 5b3) 状态一致性：rejected eligible/无 label；labeled/completed maturity；
  #     非 failed attempts/error/next_retry；failed/decision 决策字段全空
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash) VALUES ('b12', 1, 'S', '4h', 1, '{}', 'rejected', '{}', '0123456789abcdef', true, '[]', '{}');" \
    && die "CHECK 未拒绝 rejected eligible=true"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash) VALUES ('b13', 1, 'S', '4h', 1, '{}', 'labeled', '{}', '0123456789abcdef', false, '[]', '{}');" \
    && die "CHECK 未拒绝 labeled eligible=false"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash, label_json, label_as_of, maturity_window) VALUES ('b14', 1, 'S', '4h', 1, '{}', 'labeled', '{}', '0123456789abcdef', true, '[]', '{}', '{}', 100, 40);" \
    && die "CHECK 未拒绝 labeled maturity=40"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash, label_json, label_as_of, maturity_window) VALUES ('b15', 1, 'S', '4h', 1, '{}', 'completed', '{}', '0123456789abcdef', true, '[]', '{}', '{}', 100, 5);" \
    && die "CHECK 未拒绝 completed maturity=5"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash, attempts) VALUES ('b16', 1, 'S', '4h', 1, '{}', 'pending', '{}', '0123456789abcdef', true, '[]', '{}', 1);" \
    && die "CHECK 未拒绝非 failed attempts=1"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash, last_error) VALUES ('b17', 1, 'S', '4h', 1, '{}', 'rejected', '{}', '0123456789abcdef', false, '[]', '{}', 'boom');" \
    && die "CHECK 未拒绝非 failed last_error"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash, next_retry_at) VALUES ('b18', 1, 'S', '4h', 1, '{}', 'completed', '{}', '0123456789abcdef', true, '[]', '{}', 999);" \
    && die "CHECK 未拒绝 completed next_retry_at"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, failure_stage, decision_json, decision_digest, eligible, reasons, config_hash) VALUES ('b19', 1, 'S', '4h', 1, '{}', 'failed', 'decision', '{}', '0123456789abcdef', true, '[]', '{}');" \
    && die "CHECK 未拒绝 failed/decision 带 decision 字段"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, failure_stage, decision_json, decision_digest, eligible, reasons, config_hash, label_json, label_as_of) VALUES ('b20', 1, 'S', '4h', 1, '{}', 'failed', 'decision', NULL, NULL, NULL, NULL, NULL, '{}', 100);" \
    && die "CHECK 未拒绝 failed/decision 带 label"
  # 5b4) 合法：failed/decision 决策全空 + 非 failed backoff（pending+next_retry_at）
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -q \
    -c "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, failure_stage, attempts, next_retry_at, last_error) VALUES ('ok3', 1, 'BINANCE:BTCUSDT', '4h', 100, '{}', 'failed', 'decision', 1, 9999999999, 'err');" \
    || die "合法 failed/decision（决策全空）插入失败"
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -q \
    -c "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, decision_json, decision_digest, eligible, reasons, config_hash, label_json, label_as_of, maturity_window, attempts, next_retry_at) VALUES ('ok4', 1, 'BINANCE:BTCUSDT', '4h', 100, '{}', 'pending', '{}', '0123456789abcdef', true, '[]', '{}', '{}', 100, NULL, 0, 9999999999);" \
    || die "合法 pending censored + 缺数据 backoff 插入失败"
  # 5b5) failed 三层契约：attempts>=1、last_error 非空白（btrim）、next_retry_at 非空
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, failure_stage, attempts, next_retry_at, last_error) VALUES ('b21', 1, 'S', '4h', 1, '{}', 'failed', 'decision', 0, 9999999999, 'err');" \
    && die "CHECK 未拒绝 failed attempts=0"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, failure_stage, attempts, next_retry_at, last_error) VALUES ('b22', 1, 'S', '4h', 1, '{}', 'failed', 'decision', 1, NULL, 'err');" \
    && die "CHECK 未拒绝 failed next_retry_at 空"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, failure_stage, attempts, next_retry_at, last_error) VALUES ('b23', 1, 'S', '4h', 1, '{}', 'failed', 'decision', 1, 9999999999, NULL);" \
    && die "CHECK 未拒绝 failed last_error 空"
  _bad_insert "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, failure_stage, attempts, next_retry_at, last_error) VALUES ('b24', 1, 'S', '4h', 1, '{}', 'failed', 'decision', 1, 9999999999, '   ');" \
    && die "CHECK 未拒绝 failed last_error 全空白（btrim）"
  # 合法 failed（attempts>=1 + last_error 非空白 + next_retry_at 非空）可插
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -q \
    -c "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, failure_stage, attempts, next_retry_at, last_error) VALUES ('ok5', 1, 'BINANCE:BTCUSDT', '4h', 100, '{}', 'failed', 'decision', 2, 9999999999, '  boom  ');" \
    || die "合法 failed（attempts>=1 + 非空白 error + next_retry_at）插入失败"
  log "PASS: 非法行全部被 CHECK 拒绝（含 label_bound/状态一致性/重试卫生）"

  # 5c) 合法 decision 失败态（唯一行，decision 空）必须能插入
  docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -q \
    -c "INSERT INTO m3_shadow_events(source_dedupe_key, source_event_id, symbol, timeframe, signal_time, source_snapshot, status, failure_stage, attempts, next_retry_at, last_error) VALUES ('ok1', 1, 'BINANCE:BTCUSDT', '4h', 100, '{\"dedupe_key\":\"ok1\"}', 'failed', 'decision', 1, 9999999999, 'err');" \
    || die "合法 decision 失败态插入失败"
  log "PASS: 合法 decision 失败态可插入"

  # 6) rollback 确认词失败路径：错误输入 → 函数中止且表保留
  #    （直接调用函数而非 bash "$0"，避免参数解析歧义误操作其他库）
  log "错误确认词 rollback（必须失败且不删表）..."
  if printf 'WRONG PHRASE\n' | cmd_rollback "$TEST_DB" >/dev/null 2>&1; then
    die "错误确认词竟然回滚成功"
  fi
  for t in $M3_TABLES; do
    docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -tAc \
      "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='$t';" \
      | grep -q 1 || die "错误确认词后表 $t 被误删"
  done
  log "PASS: rollback 确认词失败路径安全（错误词不删表）"

  # 7) rollback 正确确认词 → 只删 M3 表，V1 六表保留
  log "正确确认词 rollback ..."
  printf '%s\n' "$CONFIRM_PHRASE" | cmd_rollback "$TEST_DB" >/dev/null 2>&1
  for t in $M3_TABLES; do
    if docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -tAc \
      "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='$t';" \
      | grep -q 1; then
      die "rollback 后表 $t 仍存在"
    fi
  done
  for t in $V1_TABLES; do
    docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -tAc \
      "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='$t';" \
      | grep -q 1 || die "rollback 误删 V1 表 $t"
  done
  log "PASS: rollback 只删两张 M3 表，V1 六表保留"

  echo ""
  echo "全部通过：建表幂等 / 唯一键 / 无 V1 FK / 心跳单例 / rollback 确认词与范围"
}

main() {
  local db="$PG_DB" cmd=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --db) db="${2:?--db 需要参数}"; shift 2 ;;
      status|apply|rollback|test) cmd="$1"; shift ;;
      *) _usage; exit 1 ;;
    esac
  done
  case "$cmd" in
    status)   cmd_status "$db" ;;
    apply)    cmd_apply "$db" ;;
    rollback) cmd_rollback "$db" ;;
    test)     cmd_test ;;
    *) _usage; exit 1 ;;
  esac
}

# 仅作为主脚本执行时进入命令行入口；被 source（测试复用）时不执行
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
