#!/usr/bin/env bash
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

if [[ ${EUID} -ne 0 ]]; then printf '%s\n' 'Run as root.' >&2; exit 1; fi
check_only=0
retry_incomplete=0
if [[ "${1:-}" == --check ]]; then check_only=1; shift; fi
if [[ "${1:-}" == --retry-incomplete ]]; then retry_incomplete=1; shift; fi
if [[ "${1:-}" == --rollback || "${1:-}" == --downgrade || "${1:-}" == --rollback-history ]]; then
    if (( check_only || retry_incomplete )); then echo 'Invalid rollback flags' >&2; exit 1; fi
    source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
    exec /usr/bin/python3 -u "$source_dir/scripts/update_cli.py" "$@"
fi
if [[ $# -gt 4 || $# -eq 3 ]]; then printf 'Usage: %s [--check|--retry-incomplete] [HOSTNAME [STORAGE_MOUNT [TLS_FULLCHAIN TLS_KEY]]|--update [RELEASE.tar.gz]]\n' "$0" >&2; exit 1; fi
if [[ "${1:-}" == --update ]]; then
    if (( check_only || retry_incomplete )) || [[ $# -gt 2 ]]; then printf 'Usage: %s --update [RELEASE.tar.gz]\n' "$0" >&2; exit 1; fi
    source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
    shift
    exec /usr/bin/python3 -u "$source_dir/scripts/update_cli.py" "$@"
fi
host=${1:-}
if [[ -z "$host" ]]; then
    if [[ -t 0 ]]; then read -r -p 'Nazwa DNS serwera [nvr.local]: ' host; fi
    host=${host:-nvr.local}
fi
host=$(printf '%s' "$host" | LC_ALL=C tr '[:upper:]' '[:lower:]')
default_storage=0
if [[ $# -lt 2 ]]; then storage=/opt/sunhill-nvr/media; default_storage=1; else storage=$2; fi
tls_cert=${3:-}
tls_key=${4:-}
local_tls=${SUNHILL_GENERATE_LAN_TLS:-0}
if [[ $# -lt 4 && "$host" == *.local && -z "${SUNHILL_GENERATE_LAN_TLS+x}" ]]; then local_tls=1; fi
if [[ "$local_tls" != 0 && "$local_tls" != 1 ]]; then printf '%s\n' 'Invalid SUNHILL_GENERATE_LAN_TLS value.' >&2; exit 1; fi
if [[ "$local_tls" == 1 && $# -eq 4 ]]; then printf '%s\n' 'Choose local TLS generation or supplied certificate, not both.' >&2; exit 1; fi
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=scripts/install_helpers.sh
. "$source_dir/scripts/install_helpers.sh"
# Fail before changing packages or creating accounts when the source or CPU
# cannot be installed. A symlink anywhere in the source tree is rejected.
arch=$(uname -m)
case "$arch" in
 x86_64) asset=go2rtc_linux_amd64; digest=20b46c6910734cb05ecefb234066f36868ff19b42ba1418a7a32e13a3e035712 ;;
 aarch64|arm64) asset=go2rtc_linux_arm64; digest=af416f22b21a24ab83b8d78642190b4932ae695b4e386853f11d1b0319781763 ;;
 *) printf 'Unsupported architecture: %s\n' "$arch" >&2; exit 1 ;;
esac
if [[ -L "$source_dir/src/sunhill_nvr" || ! -d "$source_dir/src/sunhill_nvr" ]] ||
   [[ -n "$(find "$source_dir/src/sunhill_nvr" -type l -print -quit)" ]]; then
    printf '%s\n' 'Application source is missing or contains symlinks.' >&2; exit 1
fi
if [[ $# -eq 4 && ( -z "$tls_cert" || -z "$tls_key" ) ]]; then
    printf '%s\n' 'Both TLS certificate and key are required.' >&2; exit 1
fi
validate_tls_inputs "$tls_cert" "$tls_key"
if [[ "$local_tls" == 1 ]] && ! command -v openssl >/dev/null; then
    printf '%s\n' 'OpenSSL is required before local TLS preflight.' >&2; exit 1
fi

if [[ ! -f /etc/os-release ]]; then printf '%s\n' 'Cannot identify OS.' >&2; exit 1; fi
# shellcheck source=/dev/null
. /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 26.04 ]]; then
    printf '%s\n' 'Only Ubuntu 26.04 LTS is supported.' >&2; exit 1
fi
storage_probe=$storage
if (( default_storage )); then
    storage_probe=/opt
else
    if [[ "$storage" != /* || -L "$storage" || ! -d "$storage" ]]; then printf '%s\n' 'Storage path must be an existing real directory.' >&2; exit 1; fi
    storage=$(cd -- "$storage" && pwd -P)
    storage_probe=$storage
fi
validate_storage_targets "$storage"
storage_group=${SUNHILL_STORAGE_GROUP:-}
validate_storage_group "$storage_group"
mount_target=$(findmnt --noheadings --output TARGET --target "$storage_probe")
allow_root_storage=${SUNHILL_ALLOW_ROOT_STORAGE:-$default_storage}
validate_storage_mount "$storage" "$mount_target" "$allow_root_storage"
enroll_options=()
if [[ "$mount_target" == / ]]; then
    enroll_options+=(--allow-rootfs)
    printf '%s\n' 'Storage is on the system filesystem; the recording reserve is shared with the operating system.' >&2
fi
refuse_existing_paths /etc/sunhill-nvr /var/lib/sunhill-nvr /var/lib/sunhill-control \
    /etc/nginx/conf.d/sunhill-nvr.conf /etc/systemd/system/nginx.service.d/sunhill-recovery.conf
if (( retry_incomplete )); then
    if (( ! default_storage )) || [[ -n "$storage_group" ]]; then
        printf '%s\n' 'Retry is limited to the failed default-storage installation.' >&2; exit 1
    fi
    validate_incomplete_install /opt/sunhill-nvr sunhill /etc/systemd/system
else
    refuse_existing_paths /opt/sunhill-nvr
    if id sunhill >/dev/null 2>&1; then printf '%s\n' 'User sunhill already exists. Refusing to reuse it.' >&2; exit 1; fi
    if getent group sunhill >/dev/null 2>&1; then printf '%s\n' 'Group sunhill already exists. Refusing to reuse it.' >&2; exit 1; fi
fi
if ! command -v systemctl >/dev/null; then
    printf '%s\n' 'systemctl is required.' >&2; exit 1
fi
PYTHONPATH="$source_dir/src" /usr/bin/python3 - "$host" "$storage_probe" "$tls_cert" "$local_tls" <<'PY'
import sys
from sunhill_nvr.nginx_config import HOST
from sunhill_nvr import mount,proc_scan,install_preflight
from pathlib import Path
if not HOST.fullmatch(sys.argv[1]):raise SystemExit('Invalid hostname')
if any(c in sys.argv[2] for c in '\n\r\x00'):raise SystemExit('Invalid storage path')
try:mount.require_local(mount.inspect(Path(sys.argv[2])))
except (ValueError,RuntimeError) as exc:raise SystemExit(str(exc))
try:proc_scan.require_proc_visibility()
except proc_scan.IncompleteScan as exc:raise SystemExit(str(exc))
try:install_preflight.require_systemd_operational()
except ValueError as exc:raise SystemExit(str(exc))
try:install_preflight.require_ports_free(lan_tls=bool(sys.argv[3]) or sys.argv[4]=='1',rtsp_only=True)
except (ValueError,OSError) as exc:raise SystemExit(str(exc))
PY
tmp=$(mktemp -d /tmp/sunhill-install.XXXXXXXX)
identity_created=0
installation_started=0
package_install_started=0
uid=''
gid=''
cleanup_install() {
    local status=$?
    if (( status != 0 && installation_started == 1 )); then
        # Stop only units created by this attempt. Do not remove archive files,
        # account data or the recovery unit required by an existing Nginx.
        local unit target
        for unit in "$tmp"/generated/units/*.timer "$tmp"/generated/units/*.service; do
            [[ -f "$unit" ]] || continue
            target=$(basename "$unit")
            [[ "$target" == sunhill-upgrade-recover.service ]] && continue
            if [[ -f "/etc/systemd/system/$target" && ! -L "/etc/systemd/system/$target" ]]; then
                systemctl disable --now "$target" >/dev/null 2>&1 || true
            fi
        done
        printf '%s\n' 'Installation stopped after creating system files. Sunhill services were asked to stop; data was not removed.' >&2
        printf '%s\n' 'Do not retry over the partial installation. Inspect: systemctl --failed; journalctl -u sunhill-controller -u sunhill-web -u nginx' >&2
    fi
    if (( status != 0 && identity_created == 1 && installation_started == 0 )); then
        if ! rollback_fresh_identity sunhill "$uid" "$gid"; then
            printf '%s\n' 'Automatic cleanup of the newly created service account failed.' >&2
        fi
    fi
    rm -rf -- "$tmp"
}
trap cleanup_install EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
PYTHONPATH="$source_dir/src" /usr/bin/python3 - "$tls_cert" "$tls_key" "$tmp" <<'PY'
from pathlib import Path
import sys
from sunhill_nvr.release import _snapshot
certificate,key,directory=sys.argv[1:]
root=Path(directory)
if certificate:
    _snapshot(Path(certificate),root/'fullchain.pem',2_000_000)
    _snapshot(Path(key),root/'key.pem',2_000_000)
PY
if [[ -n "$tls_cert" ]]; then tls_cert="$tmp/fullchain.pem"; tls_key="$tmp/key.pem"; fi
if [[ "$local_tls" == 1 ]]; then
    PYTHONPATH="$source_dir/src" /usr/bin/python3 - "$host" "$tmp" <<'PY'
from pathlib import Path
import sys
from sunhill_nvr.tls import generate_local
generate_local(sys.argv[1],Path(sys.argv[2]))
PY
    tls_cert="$tmp/fullchain.pem"; tls_key="$tmp/key.pem"
fi

# Render and validate configuration and unit targets before package changes.
mkdir "$tmp/generated"
PYTHONPATH="$source_dir/src" /usr/bin/python3 - "$host" "$storage" "$tmp" "$tls_cert" <<'PYCODE'
import json,sys
from pathlib import Path
from sunhill_nvr.config import parse
from sunhill_nvr.deployment import generate,preflight_unit_targets
host,storage,directory,certificate=sys.argv[1:]
raw={'hostname':host,'database':'/opt/sunhill-nvr/data/sunhill.sqlite',
     'archive':storage+'/kamery','staging':storage+'/.sunhill-staging',
     'exports':storage+'/.sunhill-exports','archive_lock':'/opt/sunhill-nvr/data/archive.lock',
     'activity_socket':'/run/sunhill-nvr/activity.sock','control_state':'/opt/sunhill-nvr/control',
     'timezone':'Europe/Warsaw','minimum_free':10_000_000_000,'free_buffer':5_000_000_000}
config=parse(raw);root=Path(directory)
(root/'config.json').write_text(json.dumps(raw,indent=2)+'\n',encoding='utf-8')
options={'tls_certificate':Path('/opt/sunhill-nvr/config/tls/current/fullchain.pem'),'tls_key':Path('/opt/sunhill-nvr/config/tls/current/key.pem')} if certificate else {}
generate(config,root/'generated',rtsp_only=True,**options)
preflight_unit_targets(root/'generated'/'units',Path('/etc/systemd/system'))
PYCODE

if command -v openssl >/dev/null 2>&1; then
    if [[ -n "$tls_cert" ]]; then
        PYTHONPATH="$source_dir/src" /usr/bin/python3 - "$tls_cert" "$tls_key" "$host" "$local_tls" <<'PY'
from pathlib import Path
import sys
from sunhill_nvr.tls import validate
options={'ca_bundle':Path(sys.argv[1]).parent/'local-ca.pem'} if sys.argv[4]=='1' else {}
validate(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3],**options)
PY
    fi
else
    printf '%s\n' 'OpenSSL is not installed; certificate validation will run after package installation.' >&2
fi
if (( check_only )); then
    printf '%s\n' 'Preflight passed. No packages, accounts, services or recordings were changed.'
    printf '%s\n' 'Account permissions, package availability and downloaded files are checked during installation.'
    exit 0
fi

# Download and verify the pinned gateway while all system changes are still
# avoidable. This uses the system Python and CA store, rather than installing
# curl first and leaving packages behind on a network/checksum error.
if [[ -n "${SUNHILL_GO2RTC_FILE:-}" ]]; then
    PYTHONPATH="$source_dir/src" /usr/bin/python3 -m sunhill_nvr.install_download --local \
        "$SUNHILL_GO2RTC_FILE" "$digest" "$tmp/go2rtc"
else
    PYTHONPATH="$source_dir/src" /usr/bin/python3 -m sunhill_nvr.install_download \
        "https://github.com/AlexxIT/go2rtc/releases/download/v1.9.15/$asset" "$digest" "$tmp/go2rtc"
fi

# Verify existing archive directories before creating application files. Never
# change ownership or permissions of the user's pre-existing recordings.
useradd_options=(--user-group --no-create-home --shell /usr/sbin/nologin)
if [[ -n "$storage_group" ]]; then useradd_options+=(--groups "$storage_group"); fi
if (( ! retry_incomplete )); then
    useradd "${useradd_options[@]}" sunhill
    identity_created=1
fi
uid=$(id -u sunhill)
gid=$(id -g sunhill)
if (( uid < 1000 || gid < 1000 )); then printf '%s\n' 'Application UID/GID must be unprivileged.' >&2; exit 1; fi
if (( ! default_storage )); then validate_storage_access "$storage" sunhill; fi
if (( ! retry_incomplete )) && [[ -d "$storage/kamery" ]]; then
    printf '%s\n' 'Checking read and rotation permissions in the existing archive; this may take time.'
    runuser -u sunhill -- /usr/bin/python3 - "$storage/kamery" < "$source_dir/scripts/preflight_archive.py"
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
package_install_started=1
packages=(python3 gunicorn ffmpeg openssl nginx curl util-linux tzdata ca-certificates)
apt-get install -y "${packages[@]}"
validate_executable_dependencies /usr/bin/python3 /usr/bin/gunicorn /usr/bin/ffmpeg /usr/bin/ffprobe \
    /usr/bin/openssl /usr/sbin/nginx /usr/bin/findmnt /usr/bin/systemctl \
    /usr/bin/systemd-analyze /usr/sbin/runuser
if ! /usr/bin/python3 -c 'import gunicorn' >/dev/null 2>&1; then
    printf '%s\n' 'Gunicorn cannot be imported by the system Python.' >&2; exit 1
fi
if [[ -n "$tls_cert" ]]; then
    PYTHONPATH="$source_dir/src" /usr/bin/python3 - "$tls_cert" "$tls_key" "$host" "$local_tls" <<'PY'
from pathlib import Path
import sys
from sunhill_nvr.tls import validate
options={'ca_bundle':Path(sys.argv[1]).parent/'local-ca.pem'} if sys.argv[4]=='1' else {}
validate(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3],**options)
PY
fi
version=$(PYTHONPATH="$source_dir/src" /usr/bin/python3 -c 'from sunhill_nvr import __version__; print(__version__)')
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 1
if (( retry_incomplete )); then
    validate_incomplete_install /opt/sunhill-nvr sunhill /etc/systemd/system
    # Preserve the entire previous tree; never delete it or reuse its contents.
    backup=$(mktemp -d /opt/sunhill-nvr-incomplete.XXXXXXXX)
    mv -T -- /opt/sunhill-nvr "$backup/original"
    printf 'Previous incomplete installation preserved in %s/original\n' "$backup"
fi
installation_started=1
prepare_application_directories /opt/sunhill-nvr "$version" sunhill sunhill "$default_storage"
if [[ -n "$tls_cert" ]]; then
    initial_tls=/opt/sunhill-nvr/config/tls/versions/00000000000000000000000000000000
    install -d -o root -g root -m 0700 "$initial_tls"
    install -o root -g root -m 0644 "$tls_cert" "$initial_tls/fullchain.pem"
    install -o root -g root -m 0600 "$tls_key" "$initial_tls/key.pem"
    if [[ "$local_tls" == 1 ]]; then
        install -o root -g root -m 0644 "$tmp/local-ca.pem" "$initial_tls/local-ca.pem"
        printf '%s\n' 'LAN CA: /opt/sunhill-nvr/config/tls/current/local-ca.pem (copy via trusted channel to clients).'
    fi
    ln -s versions/00000000000000000000000000000000 /opt/sunhill-nvr/config/tls/current
fi
cp -R -- "$source_dir/src/sunhill_nvr" "/opt/sunhill-nvr/releases/$version"/app/
cp -R -- "$source_dir/src/sunhill_nvr" /opt/sunhill-nvr/recovery/app/
for copied in "/opt/sunhill-nvr/releases/$version"/app/sunhill_nvr /opt/sunhill-nvr/recovery/app/sunhill_nvr; do
    if [[ -n "$(find "$copied" -type l -print -quit)" ]]; then printf '%s\n' 'Symlinks are not allowed in application code.' >&2; exit 1; fi
    find "$copied" -type d -name __pycache__ -prune -exec rm -rf -- {} +
    find "$copied" -type f -name '*.pyc' -delete
    find "$copied" -type d -exec chmod 0755 {} +
    find "$copied" -type f -exec chmod 0644 {} +
done
chown -R root:root "/opt/sunhill-nvr/releases/$version"
chmod -R go-w "/opt/sunhill-nvr/releases/$version"
chown -R root:root /opt/sunhill-nvr/recovery
chmod -R go-w /opt/sunhill-nvr/recovery
# Persist the exact manifest used by later versioned updates, including when
# this installer was invoked from a development checkout rather than a tarball.
PYTHONPATH="$source_dir/src" /usr/bin/python3 - "$version" <<'PYCODE'
import hashlib,json,sys
from pathlib import Path
root=Path('/opt/sunhill-nvr/releases')/sys.argv[1]
files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'app').rglob('*') if p.is_file()}
manifest=root/'manifest.json';manifest.write_text(json.dumps({'version':sys.argv[1],'files':files},sort_keys=True),encoding='utf-8');manifest.chmod(0o644)
PYCODE
ln -s "releases/$version" /opt/sunhill-nvr/current
runuser -u sunhill -- env PYTHONPATH=/opt/sunhill-nvr/current/app /usr/bin/python3 -c 'import sunhill_nvr; print("Application version:", sunhill_nvr.__version__)'
for leaf in kamery .sunhill-staging .sunhill-exports; do
    dest="$storage/$leaf"
    if [[ -L "$dest" ]]; then printf 'Refusing symlink: %s\n' "$dest" >&2; exit 1; fi
    if [[ ! -e "$dest" ]]; then install -d -o sunhill -g sunhill -m 0750 "$dest"; fi
    if [[ ! -d "$dest" ]] || ! account_can_access sunhill -w "$dest" || ! account_can_access sunhill -x "$dest" || ! account_can_access sunhill -r "$dest"; then printf 'Directory not accessible by application: %s\n' "$dest" >&2; exit 1; fi
done

install -o root -g root -m 0755 "$tmp/go2rtc" /opt/sunhill-nvr/bin/go2rtc
install -o root -g sunhill -m 0640 "$tmp/config.json" /opt/sunhill-nvr/config/config.json
setup_token=''
if [[ "${SUNHILL_INSTALL_CONSOLE_ADMIN:-}" == 1 ]]; then
    runuser -u sunhill -- env PYTHONPATH=/opt/sunhill-nvr/current/app /usr/bin/python3 -m sunhill_nvr.cli init
else
    setup_token=$(runuser -u sunhill -- env PYTHONPATH=/opt/sunhill-nvr/current/app /usr/bin/python3 -m sunhill_nvr.cli init-setup)
fi
runuser -u sunhill -- env PYTHONPATH=/opt/sunhill-nvr/current/app /usr/bin/python3 -m sunhill_nvr.cli migrate --rtsp-only
runuser -u sunhill -- env PYTHONPATH=/opt/sunhill-nvr/current/app /usr/bin/python3 -m sunhill_nvr.cli enroll-storage "${enroll_options[@]}"
for file in "$tmp"/generated/units/*; do
    target="/etc/systemd/system/$(basename "$file")"
    if [[ -e "$target" || -L "$target" ]]; then printf 'Refusing existing unit: %s\n' "$target" >&2; exit 1; fi
    install -o root -g root -m 0644 "$file" "$target"
done
PYTHONPATH=/opt/sunhill-nvr/current/app /usr/bin/python3 - "$tmp/generated/units" "$tmp/managed-units.json" <<'PY'
import json,re,sys
from pathlib import Path
names=sorted(p.name for p in Path(sys.argv[1]).iterdir())
if not names or any(not re.fullmatch(r'sunhill-[a-z0-9-]+\.(?:service|timer)',name) for name in names):
    raise SystemExit('Invalid managed service name')
Path(sys.argv[2]).write_text(json.dumps(names,separators=(',',':'))+'\n',encoding='utf-8')
PY
install -o root -g root -m 0644 "$tmp/managed-units.json" /opt/sunhill-nvr/control/upgrade/managed-units.json
install -o root -g root -m 0644 "$tmp/generated/lifecycle.json" /opt/sunhill-nvr/control/upgrade/lifecycle.json
install -o root -g root -m 0644 "$tmp/generated/nginx.conf" /etc/nginx/conf.d/sunhill-nvr.conf
install -d -o root -g root -m 0755 /etc/systemd/system/nginx.service.d
printf '%s\n' '[Unit]' 'Requires=sunhill-upgrade-recover.service' 'After=sunhill-upgrade-recover.service' > "$tmp/sunhill-recovery.conf"
install -o root -g root -m 0644 "$tmp/sunhill-recovery.conf" /etc/systemd/system/nginx.service.d/sunhill-recovery.conf
nginx -t
# Validate exactly the paths and account used by the boot recovery manager
# before queuing systemd jobs. On a clean install no recovery journal exists.
PYTHONPATH=/opt/sunhill-nvr/recovery/app /usr/bin/python3 -c 'from sunhill_nvr.upgrade_admin import context; context()'
systemctl daemon-reload
start_initial_services
systemctl enable --now sunhill-activity.service sunhill-web.service sunhill-live-proxy.service sunhill-export.service \
    sunhill-index.service sunhill-recorder.service sunhill-health.service sunhill-stream-health.service sunhill-clock.service sunhill-retention.service sunhill-retention-preview.service \
    sunhill-controller.timer sunhill-upgrade-ready.service sunhill-upgrade-apply.timer
systemctl enable --now nginx
systemctl reload nginx
health_options=(--rtsp-only)
PYTHONPATH=/opt/sunhill-nvr/current/app /usr/bin/python3 -m sunhill_nvr.install_health "$host" "${health_options[@]}"
/usr/bin/python3 -u "$source_dir/scripts/update_cli.py" --access
printf 'Sunhill NVR %s installed.\n' "$version"
printf '%s\n' 'Cloudflare Tunnel origin: http://127.0.0.1:8080'
if [[ -n "$setup_token" ]]; then
    printf 'One-time setup code (valid 24 hours): %s\n' "$setup_token"
    printf 'Open https://%s and create the first administrator in the setup form.\n' "$host"
    unset setup_token
fi
printf '%s\n' 'DONE'
