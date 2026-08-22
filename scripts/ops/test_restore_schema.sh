#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
#  生产同构数据库恢复测试（告警体系 V2 · 里程碑 0 验收项）
#
#  背景：生产库存在备份范围外的表（如 replay_notes）以外键引用六张告警表，
#  旧实现 pg_restore --clean 会因依赖而失败。本测试构造「生产同构库」
#  （六表 + replay_notes(FK→users) + 引用行），复用与生产 restore 完全相同的
#  _restore_db 流程，验证：
#    1. 六表恢复成功且行数与快照一致
#    2. replay_notes 数据保留、外键约束重建且生效
#    3. 源码同步恢复会删除快照外新增文件（沙箱验证）
#
#  安全：全部操作在唯一命名临时库与临时沙箱中进行，不触碰生产库/工作区；
#  结束即清理。可反复执行。
# ═══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# source 复用 alert_restore.sh 的 _restore_db / _sync_source / _require_backup / _table_rows
source "$SCRIPT_DIR/alert_restore.sh"

BACKUP_DIR="${1:-$(ls -dt "$REPO_ROOT"/reports/alert-backups/*/ 2>/dev/null | grep -v '/rescue-' | head -1)}"
[ -n "$BACKUP_DIR" ] || { echo "错误: 未找到快照目录（reports/alert-backups/*/）" >&2; exit 1; }
# 规范化（去掉尾斜杠）
BACKUP_DIR="$(echo "$BACKUP_DIR" | sed 's#/$##')"

TEST_DB="alert_schema_test_$(date -u +%Y%m%dT%H%M%SZ)_$$"
FAIL_DB="alert_schema_fail_$(date -u +%Y%m%dT%H%M%SZ)_$$"
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/alert_sync_sandbox.XXXXXX")"

cleanup() {
  docker exec "$PG_CONTAINER" dropdb -U "$PG_USER" --if-exists "$TEST_DB" >/dev/null 2>&1 || true
  docker exec "$PG_CONTAINER" dropdb -U "$PG_USER" --if-exists "$FAIL_DB" >/dev/null 2>&1 || true
  rm -rf "$SANDBOX"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

# ── 0. 备份前置校验 ────────────────────────────────────────────────────────────
echo "== 使用快照: $BACKUP_DIR =="
_require_backup "$BACKUP_DIR"

# ── 1. 构造生产同构库 ──────────────────────────────────────────────────────────
echo "== 构造生产同构库 ${TEST_DB}（六表 + replay_notes(FK→users) + 引用行）=="
docker exec "$PG_CONTAINER" createdb -U "$PG_USER" "$TEST_DB"
docker exec -i "$PG_CONTAINER" pg_restore --no-owner --no-privileges \
  -U "$PG_USER" -d "$TEST_DB" < "$BACKUP_DIR/alerts.pgdump" >/dev/null 2>&1 \
  || fail "同构库基础 restore 失败"
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -q \
  -c "CREATE TABLE replay_notes (id bigserial PRIMARY KEY, user_id integer NOT NULL REFERENCES users(id));" \
  || fail "创建 replay_notes 失败"
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -q \
  -c "INSERT INTO replay_notes(user_id) SELECT id FROM users LIMIT 1;" \
  || fail "插入 replay_notes 引用行失败"
pass "同构库构造完成（replay_notes_user_id_fkey 就位）"

# ── 2. 执行与生产 restore 相同的数据恢复流程 ──────────────────────────────────
echo "== 执行 _restore_db（与生产 restore 相同流程）=="
_restore_db "$BACKUP_DIR" "$TEST_DB"
pass "_restore_db 完成（旧实现在此步因 replay_notes FK 依赖失败）"

# ── 3. 断言：六表行数 / replay_notes 数据 / FK 重建与生效 ─────────────────────
echo "== 断言 =="
for t in $ALERT_TABLES; do
  expected="$(_table_rows "$BACKUP_DIR" "$t")"
  actual="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -tAc \
    "SELECT count(*) FROM $t;" | tr -d '[:space:]')"
  [ "$expected" = "$actual" ] || fail "表 $t 行数不一致: 快照=$expected 恢复库=$actual"
done
pass "六表行数与快照一致"

replay_rows="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -tAc \
  "SELECT count(*) FROM replay_notes;" | tr -d '[:space:]')"
[ "$replay_rows" = "1" ] || fail "replay_notes 数据丢失: $replay_rows"
pass "replay_notes 数据保留（1 行）"

fk_count="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -tAc \
  "SELECT count(*) FROM pg_constraint WHERE conname='replay_notes_user_id_fkey' AND contype='f';" | tr -d '[:space:]')"
[ "$fk_count" = "1" ] || fail "replay_notes_user_id_fkey 未重建"
pass "replay_notes_user_id_fkey 已重建"

if docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$TEST_DB" -q \
  -c "INSERT INTO replay_notes(user_id) VALUES (999999);" >/dev/null 2>&1; then
  fail "外键未生效：非法 user_id 插入成功"
fi
pass "外键约束生效（非法引用被拒绝）"

# ── 3b. 失败自动回滚场景：快照后新增用户及 replay_notes 引用新用户 ──────────
echo "== 失败回滚场景：快照后新增用户 + replay_notes 引用新用户 =="
docker exec "$PG_CONTAINER" createdb -U "$PG_USER" "$FAIL_DB"
docker exec -i "$PG_CONTAINER" pg_restore --no-owner --no-privileges \
  -U "$PG_USER" -d "$FAIL_DB" < "$BACKUP_DIR/alerts.pgdump" >/dev/null 2>&1 \
  || fail "失败场景基础 restore 失败"
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$FAIL_DB" -q \
  -c "CREATE TABLE replay_notes (id bigserial PRIMARY KEY, user_id integer NOT NULL REFERENCES users(id));" \
  || fail "失败场景创建 replay_notes 失败"
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$FAIL_DB" -q \
  -c "INSERT INTO replay_notes(user_id) VALUES (1);" \
  || fail "失败场景插入引用行失败"
# 快照后新增用户 2，并在 replay_notes 中引用它（回滚后将成为孤儿引用）
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$FAIL_DB" -q \
  -c "INSERT INTO users(id, username, password_hash, created_at) VALUES (2, 'u2', 'x', now());" \
  || fail "失败场景新增用户失败"
docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$FAIL_DB" -q \
  -c "INSERT INTO replay_notes(user_id) VALUES (2);" \
  || fail "失败场景引用新用户失败"
pass "现场就绪（users=2，replay_notes=2 含引用新用户）"

if _restore_db "$BACKUP_DIR" "$FAIL_DB"; then
  fail "期望恢复失败（重建外键遇孤儿行），但 _restore_db 返回成功"
fi
pass "_restore_db 失败（重建外键被孤儿行拒绝）"

# 失败后现场必须完全复原：users=2、replay_notes=2、外键存在且生效
users_now="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$FAIL_DB" -tAc \
  "SELECT count(*) FROM users;" | tr -d '[:space:]')"
[ "$users_now" = "2" ] || fail "失败后 users 行数异常: $users_now（期望 2，现场应复原）"
replay_now="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$FAIL_DB" -tAc \
  "SELECT count(*) FROM replay_notes;" | tr -d '[:space:]')"
[ "$replay_now" = "2" ] || fail "失败后 replay_notes 行数异常: $replay_now（期望 2，现场应复原）"
fk_now="$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$FAIL_DB" -tAc \
  "SELECT count(*) FROM pg_constraint WHERE conname='replay_notes_user_id_fkey' AND contype='f';" | tr -d '[:space:]')"
[ "$fk_now" = "1" ] || fail "失败后外键缺失: $fk_now（期望 1，事务应回滚复原）"
if docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$FAIL_DB" -q \
  -c "INSERT INTO replay_notes(user_id) VALUES (999999);" >/dev/null 2>&1; then
  fail "失败后外键未生效"
fi
pass "失败后现场自动复原（users=2 / replay_notes=2 / FK 存在且生效）"

# ── 4. 源码同步恢复：删除快照外新增文件 ───────────────────────────────────────
echo "== 源码同步沙箱测试 =="
mkdir -p "$SANDBOX/backend/app" "$SANDBOX/backend/tests"
printf 'stale\n' > "$SANDBOX/backend/app/added_after_snapshot.py"
printf 'stale\n' > "$SANDBOX/backend/tests/test_added_after_snapshot.py"
_sync_source "$BACKUP_DIR" "$SANDBOX"
[ -d "$SANDBOX/backend/app" ] || fail "backend/app 未恢复"
[ -f "$SANDBOX/backend/app/core/trend_classifier.py" ] || fail "backend/app 内容未恢复"
[ -f "$SANDBOX/.env.example" ] || fail ".env.example 未恢复"
[ -f "$SANDBOX/docker-compose.yml" ] || fail "docker-compose.yml 未恢复"
[ ! -e "$SANDBOX/backend/app/added_after_snapshot.py" ] || fail "快照外新增文件未被删除"
[ ! -e "$SANDBOX/backend/tests/test_added_after_snapshot.py" ] || fail "快照外新增测试文件未被删除"
pass "源码同步：覆盖白名单路径并删除快照外新增文件"

echo ""
echo "全部通过：生产同构数据库恢复 + 源码同步语义验证 OK"
