#!/usr/bin/env bash
# Resume the known interrupted first installation, including 0.2.5 through 0.2.8.
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
[[ $EUID == 0 && $# == 0 ]] || { echo 'Run as root without arguments.' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
root=/opt/sunhill-nvr
. "$source_dir/scripts/install_helpers.sh"
started=$(date +%s)
# Serialize the whole offline repair, including startup and health checks.
exec 9>/run/sunhill-install-resume.lock
flock -n 9 || { echo "Another Sunhill resume is running." >&2; exit 1; }
report_failure() {
    local status=$?
    trap - ERR
    printf 'Resume failed (exit %s). Data and prior release preserved; inspect logs before retrying.\n' "$status" >&2
    systemctl --failed --no-pager >&2 || true
    systemctl status sunhill-upgrade-recover.service sunhill-activity.service sunhill-controller.service sunhill-ftp-policy.service sunhill-ftp.service sunhill-go2rtc.service --no-pager -l >&2 || true
    journalctl -b --since "@$started" -u sunhill-upgrade-recover.service -u sunhill-activity.service -u sunhill-controller.service -u sunhill-ftp-policy.service -u sunhill-ftp.service -u sunhill-go2rtc.service -n 100 --no-pager >&2 || true
    for unit in sunhill-controller.timer sunhill-upgrade-apply.timer sunhill-upgrade-apply.service sunhill-upgrade-ready.service sunhill-controller.service sunhill-ftp-policy.service sunhill-go2rtc.service sunhill-ftp.service sunhill-web.service sunhill-live-proxy.service sunhill-activity.service sunhill-export.service sunhill-index.service sunhill-recorder.service sunhill-health.service sunhill-stream-health.service sunhill-clock.service sunhill-retention.service sunhill-retention-preview.service; do
        systemctl stop "$unit" >/dev/null 2>&1 || true
        systemctl disable "$unit" >/dev/null 2>&1 || true
    done
    exit "$status"
}
host=$(PYTHONPATH="$source_dir/src" /usr/bin/python3 "$source_dir/scripts/resume_release.py")
trap report_failure ERR
PYTHONPATH="$source_dir/src" /usr/bin/python3 -m sunhill_nvr.apparmor_ftp "$root/config/config.json"
systemctl daemon-reload
reset_resume_failed_units sunhill-controller.service sunhill-go2rtc.service sunhill-ftp.service sunhill-ftp-policy.service
start_initial_services
systemctl enable --now sunhill-activity.service sunhill-web.service sunhill-live-proxy.service sunhill-export.service \
    sunhill-index.service sunhill-recorder.service sunhill-health.service sunhill-stream-health.service sunhill-clock.service sunhill-retention.service sunhill-retention-preview.service \
    sunhill-controller.timer sunhill-upgrade-ready.service sunhill-upgrade-apply.timer
systemctl enable --now nginx
nginx -t
systemctl reload nginx
PYTHONPATH="$root/current/app" /usr/bin/python3 -m sunhill_nvr.install_health "$host"
version=$(PYTHONPATH="$root/current/app" /usr/bin/python3 -c 'from sunhill_nvr import __version__; print(__version__)')
printf 'Sunhill NVR %s resumed and upgraded.\n' "$version"
printf 'If no administrator exists, create one with:\n'
printf '%s\n' 'runuser -u sunhill -- env PYTHONPATH=/opt/sunhill-nvr/current/app /usr/bin/python3 -m sunhill_nvr.cli complete-setup'
