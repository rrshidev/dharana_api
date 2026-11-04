#!/bin/bash
# Dharana backup - daily: pg_dump + uploads + mail + configs
# Telegram <= THRESHOLD_MB | Google Drive (rclone) > THRESHOLD_MB
# Keeps KEEP archives locally; logs to backup.log + alerts admin on failure.
#
# Security/reliability notes:
#  - single instance via flock (cron overlap cannot corrupt staging)
#  - Telegram API calls retry 3x with backoff; every attempt logs HTTP code
#    and response body (never the token/URL) so transient DNS/network errors
#    are visible instead of a bare "sendDocument failed"
#  - dump is validated with pg_restore --list before it is shipped

set -uo pipefail
# Archives contain .env and TLS private keys: never let them be group/world-readable.
umask 077

ENV_FILE="/opt/dharana/.env"
BACKUP_DIR="/opt/dharana/backups/daily"
LOG_FILE="/opt/dharana/backups/backup.log"
KEEP=14
THRESHOLD_MB=200
TG_API="https://api.telegram.org/bot"
TS=$(date +%Y%m%d_%H%M%S)
ARCHIVE="$BACKUP_DIR/dharana_backup_$TS.tar.gz"
STAGING="/tmp/dharana_backup_$TS"

BOT_TOKEN=$(grep -E '^BOT_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)
ADMIN_TG_ID=$(grep -E '^ADMIN_TELEGRAM_ID=' "$ENV_FILE" | head -1 | cut -d= -f2-)

mkdir -p "$BACKUP_DIR"

# Keep the log bounded (old copy is rotated once).
if [ -f "$LOG_FILE" ] && [ "$(stat -c%s "$LOG_FILE")" -gt 5242880 ]; then
  mv -f "$LOG_FILE" "$LOG_FILE.1"
fi

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }

# One backup at a time (cron + manual run).
exec 9>"/opt/dharana/backups/.backup.lock"
if ! flock -n 9; then
  log "ERROR: another backup is already running"
  exit 1
fi

# Telegram call with 3 attempts. Never logs the URL (it contains the token).
tg_api() {
  local method="$1"
  shift
  local attempt=1 code body resp
  while [ "$attempt" -le 3 ]; do
    resp=$(curl -sS -m 180 -w $'\n%{http_code}' -X POST "$TG_API$BOT_TOKEN/$method" "$@" 2>>"$LOG_FILE")
    code=$(printf '%s' "$resp" | tail -n1)
    body=$(printf '%s' "$resp" | sed '$d')
    if [ "$code" = "200" ] && printf '%s' "$body" | grep -q '"ok":true'; then
      log "$method OK (http $code, attempt $attempt)"
      return 0
    fi
    log "$method failed (http ${code:-none}, attempt $attempt): $(printf '%s' "$body" | head -c 500)"
    attempt=$((attempt + 1))
    [ "$attempt" -le 3 ] && sleep $((attempt * 10))
  done
  return 1
}

notify() {
  [ -n "$BOT_TOKEN" ] && [ -n "$ADMIN_TG_ID" ] || return 0
  tg_api sendMessage -d "chat_id=$ADMIN_TG_ID" --data-urlencode "text=$1" \
    || log "WARN: alert message was not delivered"
}

fail() {
  log "ERROR: $*"
  notify "[FAIL] Dharana backup FAILED ($TS): $*"
  exit 1
}

log "=== start ==="

AVAIL_MB=$(df -Pm "$BACKUP_DIR" | awk 'NR==2 {print $4}')
[ -n "$AVAIL_MB" ] && [ "$AVAIL_MB" -ge 1024 ] || fail "not enough free disk space (${AVAIL_MB:-unknown}MB)"

# --- pg_dump (Fc = custom, stream to stdout) ---
mkdir -p "$STAGING"
DUMP="$STAGING/dharana.dump"
sudo docker exec dharana-postgres pg_dump -U dharana -d dharana -Fc > "$DUMP" 2>> "$LOG_FILE" \
  || fail "pg_dump failed"
[ -s "$DUMP" ] || fail "pg_dump produced empty file"
sudo docker exec -i dharana-postgres pg_restore --list < "$DUMP" > /dev/null 2>> "$LOG_FILE" \
  || fail "pg_dump archive is invalid (pg_restore --list failed)"

# --- uploads (avatars, receipts, messages) ---
[ -d "/opt/dharana/uploads" ] && mkdir -p "$STAGING/uploads" && cp -a /opt/dharana/uploads/. "$STAGING/uploads/" 2>> "$LOG_FILE"

# --- mail (Maildir volume) ---
MAIL_SRC="/var/lib/docker/volumes/mail_vmail/_data"
if [ -d "$MAIL_SRC" ]; then mkdir -p "$STAGING/mail"; cp -a "$MAIL_SRC/." "$STAGING/mail/" 2>> "$LOG_FILE"; fi

# --- configs: compose, .env, Caddyfile, watchdog, autoconfig, caddy certs ---
mkdir -p "$STAGING/configs"
for f in docker-compose.yml Caddyfile .env health_watchdog.sh; do
  [ -f "/opt/dharana/$f" ] && cp "/opt/dharana/$f" "$STAGING/configs/"
done
[ -d "/opt/dharana/autoconfig" ] && cp -a /opt/dharana/autoconfig "$STAGING/configs/autoconfig"
[ -d "/opt/dharana/caddy-data" ] && cp -a /opt/dharana/caddy-data "$STAGING/configs/caddy-data"

# --- mail-config (mail/tls + main.cf/dovecot + docker-compose + .env) ---
if [ -d "/opt/dharana/mail" ]; then
  mkdir -p "$STAGING/mail-config"
  cp -a /opt/dharana/mail/. "$STAGING/mail-config/"
  rm -rf "$STAGING/mail-config/logs"  # runtime logs are not backup data
fi

# --- tar.gz ---
tar --ignore-failed-read --warning=no-file-changed -czf "$ARCHIVE" -C "$STAGING" . 2>> "$LOG_FILE" \
  || fail "tar failed"

# --- rotate ---
ls -1t "$BACKUP_DIR"/dharana_backup_*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

# --- send ---
SIZE_MB=$(( $(stat -c%s "$ARCHIVE") / 1024 / 1024 ))
log "archive: $(basename "$ARCHIVE") size=${SIZE_MB}MB"

if [ "$SIZE_MB" -le "$THRESHOLD_MB" ]; then
  tg_api sendDocument -F "chat_id=$ADMIN_TG_ID" -F "document=@$ARCHIVE" \
    -F "caption=Dharana backup $TS (${SIZE_MB}MB)" \
    || fail "Telegram sendDocument failed after 3 attempts (size ${SIZE_MB}MB)"
  log "Telegram sent OK"
else
  if command -v rclone >/dev/null 2>&1 && rclone listremotes 2>/dev/null | grep -q '^gdrive:'; then
    rclone copy "$ARCHIVE" "gdrive:dharana_backup/" >> "$LOG_FILE" 2>&1 || fail "rclone gdrive failed"
    log "Google Drive sent OK"
  else
    log "gdrive remote not configured; archive stays locally (size=${SIZE_MB}MB)"
  fi
fi

rm -rf "$STAGING"
log "=== done ==="
notify "[OK] Dharana backup OK ($TS, ${SIZE_MB}MB)"
