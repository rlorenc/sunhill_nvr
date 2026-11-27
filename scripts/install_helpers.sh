#!/usr/bin/env bash
# Helpers used by the installer. Keep service management fail-closed.
validate_executable_dependencies() {
    local dependency
    for dependency in "$@"; do
        if [[ ! -f "$dependency" || ! -x "$dependency" ]]; then
            printf 'Required executable is missing or not executable: %s\n' "$dependency" >&2
            return 1
        fi
    done
}

refuse_existing_paths() {
    local path
    for path in "$@"; do
        if [[ -e "$path" || -L "$path" ]]; then
            printf 'Existing Sunhill path found: %s\n' "$path" >&2
            return 1
        fi
    done
}

validate_tls_inputs() {
    local certificate=$1 key=$2
    if [[ -z "$certificate" && -z "$key" ]]; then return 0; fi
    if [[ "$certificate" != /* || "$key" != /* || -L "$certificate" || -L "$key" || ! -f "$certificate" || ! -f "$key" ]]; then
        printf '%s\n' 'TLS certificate and key must be existing absolute non-symlink files.' >&2
        return 1
    fi
}

validate_storage_targets() {
    local root=$1 leaf target
    for leaf in kamery .sunhill-staging .sunhill-exports; do
        target="$root/$leaf"
        if [[ -L "$target" || ( -e "$target" && ! -d "$target" ) ]]; then
            printf 'Invalid storage directory: %s\n' "$target" >&2
            return 1
        fi
    done
}

validate_storage_mount() {
    local storage=$1 target=$2 allow_root=$3
    if [[ "$allow_root" != 0 && "$allow_root" != 1 ]]; then
        printf '%s\n' 'SUNHILL_ALLOW_ROOT_STORAGE must be 0 or 1.' >&2; return 1
    fi
    if [[ -z "$target" || "$storage" == / ]]; then
        printf '%s\n' 'Storage must be an existing dedicated directory, not /.' >&2; return 1
    fi
    if [[ "$target" == / && "$allow_root" != 1 ]]; then
        printf '%s\n' 'Storage is on the system filesystem. Use a data filesystem or explicitly set SUNHILL_ALLOW_ROOT_STORAGE=1.' >&2; return 1
    fi
}

validate_storage_group() {
    local name=$1 entry group password gid members
    [[ -z "$name" ]] && return 0
    if [[ ! "$name" =~ ^[a-z_][a-z0-9_-]{0,30}$ ]]; then
        printf '%s\n' 'Invalid storage group name.' >&2; return 1
    fi
    case "$name" in
        root|sudo|adm|disk|docker|lxd|libvirt|kvm|shadow|systemd-journal|nogroup|nobody)
            printf '%s\n' 'Privileged groups cannot be used for archive access.' >&2; return 1 ;;
    esac
    entry=$(getent group "$name") || { printf 'Storage group does not exist: %s\n' "$name" >&2; return 1; }
    IFS=: read -r group password gid members <<< "$entry"
    if [[ "$group" != "$name" || ! "$gid" =~ ^[0-9]{1,9}$ ]] || (( 10#$gid < 1000 || 10#$gid == 65534 )); then
        printf '%s\n' 'Storage group must have an unprivileged numeric GID.' >&2; return 1
    fi
}

# Check access under the actual service credentials, including supplementary
# groups. Do not depend on the platform's implementation of external test(1).
account_can_access() {
    runuser -u "$1" -- /usr/bin/python3 -c 'import os,sys; modes={"-r":os.R_OK,"-w":os.W_OK,"-x":os.X_OK}; sys.exit(0 if os.access(sys.argv[2],modes[sys.argv[1]],effective_ids=True) else 1)' "$2" "$3"
}

validate_storage_access() {
    local root=$1 account=$2 leaf target flag
    if ! account_can_access "$account" -x "$root"; then
        printf 'Application account cannot traverse storage: %s\n' "$root" >&2
        return 1
    fi
    for leaf in kamery .sunhill-staging .sunhill-exports; do
        target="$root/$leaf"
        if [[ -L "$target" || ( -e "$target" && ! -d "$target" ) ]]; then
            printf 'Invalid storage directory: %s\n' "$target" >&2
            return 1
        fi
        if [[ -d "$target" ]]; then
            for flag in -r -w -x; do
                if ! account_can_access "$account" "$flag" "$target"; then
                    printf 'Application account lacks %s access to existing directory: %s\n' "$flag" "$target" >&2
                    return 1
                fi
            done
        fi
    done
}

rollback_fresh_identity() {
    local account=$1 expected_uid=$2 expected_gid=$3 found_uid found_gid
    found_uid=$(id -u "$account" 2>/dev/null) || return 0
    found_gid=$(id -g "$account" 2>/dev/null) || return 1
    if [[ "$found_uid" != "$expected_uid" || "$found_gid" != "$expected_gid" ]]; then
        printf '%s\n' 'Application account changed during installation; refusing automatic cleanup.' >&2
        return 1
    fi
    userdel "$account" || return 1
    if getent group "$account" >/dev/null 2>&1; then
        found_gid=$(getent group "$account" | cut -d: -f3)
        if [[ "$found_gid" != "$expected_gid" ]]; then
            printf '%s\n' 'Application group changed during installation; refusing automatic cleanup.' >&2
            return 1
        fi
        groupdel "$account" || return 1
    fi
}

# Create every parent explicitly: install -d may create implicit ancestors
# with the current umask (notably uutils under umask 077).
prepare_application_directories() {
    local root=$1 version=$2 account=$3 group=$4 default_storage=$5 dir
    for dir in "$root" "$root/releases" "$root/releases/$version" "$root/releases/$version/app" \
        "$root/bin" "$root/config" "$root/config/tls" "$root/config/tls/versions" \
        "$root/recovery" "$root/recovery/app" "$root/updates"; do
        install -d -o root -g root -m 0755 "$dir" || return 1
        chmod 0755 "$dir" || return 1
    done
    for dir in "$root/data" "$root/data/upgrade-inbox"; do
        install -d -o "$account" -g "$group" -m 0700 "$dir" || return 1
    done
    for dir in "$root/control" "$root/control/upgrade"; do
        install -d -o root -g "$group" -m 0750 "$dir" || return 1
    done
    if (( default_storage )); then
        install -d -o "$account" -g "$group" -m 0750 "$root/media" || return 1
    fi
    for dir in "$root" "$root/releases/$version/app" "$root/recovery/app" "$root/config" "$root/control/upgrade"; do
        account_can_access "$account" -x "$dir" || { printf 'Application cannot traverse: %s\n' "$dir" >&2; return 1; }
    done
    for dir in "$root/data" "$root/data/upgrade-inbox"; do
        account_can_access "$account" -w "$dir" || { printf 'Application cannot write: %s\n' "$dir" >&2; return 1; }
    done
}

# Only allow the known early 0.2.0 failure: no database, config, gateway,
# installed units or recordings. No automatic deletion; the caller archives it.
validate_incomplete_install() {
    local root=$1 account=$2 units=$3 uid gid entry
    uid=$(id -u "$account") || return 1
    gid=$(id -g "$account") || return 1
    if (( uid < 1000 || gid < 1000 || uid == 65534 || gid == 65534 )); then return 1; fi
    entry=$(getent passwd "$account") || return 1
    [[ "$entry" == *:/usr/sbin/nologin ]] || return 1
    [[ "$(id -G "$account")" == "$gid" ]] || return 1
    [[ "$(getent group "$account" | cut -d: -f3)" == "$gid" ]] || return 1
    /usr/bin/python3 - "$root" "$units" <<'PY'
import os, stat, sys
from pathlib import Path
root, units = map(Path, sys.argv[1:])
def reject():
    raise SystemExit('Not the supported early failed installation; refusing retry without manual inspection.')
if root.is_symlink() or not root.is_dir() or root.stat().st_uid != 0:
    reject()
if any(units.glob('sunhill-*')) or any(units.glob('*.wants/sunhill-*')):
    reject()
current = root / 'current'
if not current.is_symlink() or os.readlink(current) != 'releases/0.2.0':
    reject()
if not (root/'releases/0.2.0/manifest.json').is_file():
    reject()
allowed = {'bin','config','config/tls','config/tls/versions','control','control/upgrade','data','data/upgrade-inbox','media','media/kamery','media/.sunhill-staging','media/.sunhill-exports','updates','releases','releases/0.2.0','releases/0.2.0/app','recovery','recovery/app'}
for directory, dirs, files in os.walk(root, followlinks=False):
    for name in dirs+files:
        path=Path(directory)/name; rel=path.relative_to(root).as_posix(); st=path.lstat()
        if rel=='current': continue
        if st.st_dev != root.stat().st_dev or os.path.ismount(path): reject()
        if stat.S_ISLNK(st.st_mode):
            if rel=='config/tls/current' and os.readlink(path)=='versions/00000000000000000000000000000000': continue
            reject()
        if rel in allowed and stat.S_ISDIR(st.st_mode): continue
        if rel=='releases/0.2.0/manifest.json' and stat.S_ISREG(st.st_mode): continue
        if rel.startswith(('releases/0.2.0/app/sunhill_nvr','recovery/app/sunhill_nvr','config/tls/versions/00000000000000000000000000000000')) and (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)): continue
        reject()
PY
}

# A stopped unit can be unloaded by systemd even though its unit file exists.
# reset-failed then returns nonzero. Only a verified inactive, installed unit
# is an acceptable fallback; bus failures and missing unit files remain fatal.
reset_resume_failed_units() {
    local unit state
    for unit in "$@"; do
        if systemctl reset-failed "$unit"; then
            continue
        fi
        if ! state=$(systemctl show --property=LoadState --property=ActiveState "$unit"); then
            printf 'Unable to inspect unit after reset failure: %s\n' "$unit" >&2
            return 1
        fi
        if ! grep -Fxq 'LoadState=loaded' <<< "$state" || ! grep -Fxq 'ActiveState=inactive' <<< "$state"; then
            printf 'Unable to reset unit: %s (%s)\n' "$unit" "$state" >&2
            return 1
        fi
        printf 'Unit already inactive: %s; continuing startup.\n' "$unit" >&2
    done
}

# Recovery must finish before the controller, and activity RPC must be ready
# before services that use it. Keep the original fail-closed dependency.
start_initial_services() {
    local unit
    for unit in sunhill-upgrade-recover.service sunhill-activity.service sunhill-controller.service; do
        if ! systemctl start "$unit"; then
            printf 'Initial service failed: %s\n' "$unit" >&2
            systemctl --failed --no-pager >&2 || true
            systemctl status sunhill-upgrade-recover.service sunhill-upgrade-ready.service sunhill-activity.service sunhill-controller.service sunhill-go2rtc.service --no-pager -l >&2 || true
            journalctl -b -u sunhill-upgrade-recover.service -u sunhill-upgrade-ready.service -u sunhill-activity.service -u sunhill-controller.service -u sunhill-go2rtc.service -n 150 --no-pager -l >&2 || true
            return 1
        fi
    done
}
