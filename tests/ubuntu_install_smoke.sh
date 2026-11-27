#!/usr/bin/env bash
# This test changes system services and mounts a filesystem. Run only on a
# disposable GitHub-hosted VM, never on an existing recorder or workstation.
set -euo pipefail
umask 077
if [[ "${GITHUB_ACTIONS:-}" != true || "${RUNNER_ENVIRONMENT:-}" != github-hosted || -z "${RUNNER_TEMP:-}" ]]; then
    printf '%s\n' 'This test requires a disposable GitHub-hosted runner.' >&2
    exit 1
fi
. /etc/os-release
if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 26.04 ]]; then
    printf '%s\n' 'This test requires Ubuntu 26.04.' >&2
    exit 1
fi
if [[ -e /opt/sunhill-nvr || -e /etc/sunhill-nvr || -e /var/lib/sunhill-nvr || -e /mnt/sunhill-ci ]]; then
    printf '%s\n' 'Existing installation or mount path detected.' >&2
    exit 1
fi
test_dir="$RUNNER_TEMP/sunhill-ci"
mkdir -m 0700 "$test_dir"
# Check default and explicit storage choices before changing any service.
sudo bash ./install.sh --check nvr.example.com >"$test_dir/default-check.log" 2>&1
grep -q 'Preflight passed' "$test_dir/default-check.log"
[[ ! -e /opt/sunhill-nvr ]]
# This directory is empty and belongs only to the disposable CI machine.
root_storage=$(sudo mktemp -d /sunhill-ci-root.XXXXXXXX)
trap 'sudo rmdir "$root_storage"' EXIT
if [[ "$(findmnt --noheadings --output TARGET --target "$root_storage")" != / ]]; then
    printf '%s\n' 'Root storage fixture is not on the root filesystem.' >&2
    exit 1
fi
if sudo bash ./install.sh --check nvr.example.com "$root_storage" >"$test_dir/root-refused.log" 2>&1; then
    printf '%s\n' 'Preflight unexpectedly accepted root storage without an explicit choice.' >&2
    exit 1
fi
grep -q 'Storage is on the system filesystem' "$test_dir/root-refused.log"
sudo env SUNHILL_ALLOW_ROOT_STORAGE=1 bash ./install.sh --check nvr.example.com "$root_storage" >"$test_dir/root-check.log" 2>&1
grep -q 'Preflight passed' "$test_dir/root-check.log"
sudo rmdir "$root_storage"
trap - EXIT
# The application stops exports at 10 GB free. Sparse ext4 provides enough
# apparent capacity while the actual synthetic clips remain small.
truncate -s 24G "$test_dir/disk.img"
mkfs.ext4 -F -q "$test_dir/disk.img"
sudo mkdir /mnt/sunhill-ci
sudo mount -o loop "$test_dir/disk.img" /mnt/sunhill-ci
# The synthetic disk root must be traversable by the later service account.
sudo chmod 0755 /mnt/sunhill-ci
sudo env PYTHONPATH="$PWD/src" python3 -c 'from sunhill_nvr.proc_scan import require_proc_visibility; require_proc_visibility()'
# Exercise the refusal path before apt or application files are touched. The
# fixture belongs to this disposable filesystem, never to a real installation.
sudo mkdir -m 0700 /mnt/sunhill-ci/kamery
printf 'existing archive fixture\n' | sudo tee /mnt/sunhill-ci/kamery/keep.txt >/dev/null
# Read-only preflight neither creates the service identity nor modifies data.
sudo bash ./install.sh --check nvr.example.com /mnt/sunhill-ci >"$test_dir/check.log" 2>&1
grep -q 'Preflight passed' "$test_dir/check.log"
if id sunhill >/dev/null 2>&1 || getent group sunhill >/dev/null 2>&1; then
    printf '%s\n' 'Read-only preflight created a service account or group.' >&2
    exit 1
fi
printf 'existing marker\n' | sudo tee /mnt/sunhill-ci/kamery/.sunhill-volume-id >/dev/null
sudo bash ./install.sh --check nvr.example.com /mnt/sunhill-ci >"$test_dir/marker.log" 2>&1
[[ "$(sudo cat /mnt/sunhill-ci/kamery/.sunhill-volume-id)" == 'existing marker' ]]
sudo rm /mnt/sunhill-ci/kamery/.sunhill-volume-id
if sudo bash ./install.sh nvr.example.com /mnt/sunhill-ci >"$test_dir/refused.log" 2>&1; then
    printf '%s\n' 'Installation unexpectedly accepted an inaccessible archive.' >&2
    exit 1
fi
grep -q 'Application account lacks -r access' "$test_dir/refused.log"
if id sunhill >/dev/null 2>&1 || getent group sunhill >/dev/null 2>&1; then
    printf '%s\n' 'Failed preflight left a service account or group behind.' >&2
    exit 1
fi
[[ "$(sudo cat /mnt/sunhill-ci/kamery/keep.txt)" == 'existing archive fixture' ]]
sudo groupadd sunhill-ci-archive
getent group sunhill-ci-archive
sudo chgrp sunhill-ci-archive /mnt/sunhill-ci/kamery
sudo chmod 0770 /mnt/sunhill-ci/kamery
sudo mkdir -p /mnt/sunhill-ci/kamery/brama/2020/01/01
sudo chgrp sunhill-ci-archive /mnt/sunhill-ci/kamery/brama /mnt/sunhill-ci/kamery/brama/2020 /mnt/sunhill-ci/kamery/brama/2020/01 /mnt/sunhill-ci/kamery/brama/2020/01/01
sudo chmod 0770 /mnt/sunhill-ci/kamery/brama /mnt/sunhill-ci/kamery/brama/2020 /mnt/sunhill-ci/kamery/brama/2020/01 /mnt/sunhill-ci/kamery/brama/2020/01/01
printf 'existing recording fixture\n' | sudo tee /mnt/sunhill-ci/kamery/brama/2020/01/01/190900.mp4 >/dev/null
sudo chgrp sunhill-ci-archive /mnt/sunhill-ci/kamery/brama/2020/01/01/190900.mp4
sudo chmod 0640 /mnt/sunhill-ci/kamery/brama/2020/01/01/190900.mp4
if ! sudo env SUNHILL_LEGACY_FTP=1 SUNHILL_STORAGE_GROUP=sunhill-ci-archive bash ./install.sh nvr.example.com /mnt/sunhill-ci >"$test_dir/install.log" 2>&1; then
    sudo namei -l /mnt/sunhill-ci/kamery >&2
    getent group sunhill-ci-archive >&2
    sed '/One-time setup code/c\One-time setup code: [redacted]' "$test_dir/install.log" >&2
    exit 1
fi
if systemctl is-active --quiet proftpd.service || systemctl is-active --quiet proftpd.socket; then
    printf '%s\n' 'The distribution ProFTPD instance must not be running alongside Sunhill FTP.' >&2
    exit 1
fi
id -nG sunhill | tr ' ' '\n' | grep -qx sunhill-ci-archive
grep -q 'nagrania: 1' "$test_dir/install.log"
[[ "$(sudo cat /mnt/sunhill-ci/kamery/brama/2020/01/01/190900.mp4)" == 'existing recording fixture' ]]
[[ "$(sudo cat /mnt/sunhill-ci/kamery/keep.txt)" == 'existing archive fixture' ]]
# The file is deliberately not a real MP4. Remove this test-only fixture before
# exercising the media indexer and keep the separate sentinel for inspection.
sudo rm /mnt/sunhill-ci/kamery/brama/2020/01/01/190900.mp4
sudo grep -qx 'proftpd (enforce)' /sys/kernel/security/apparmor/profiles
sudo -u sunhill test -r /opt/sunhill-nvr/current/app/sunhill_nvr/cli.py
sudo -u sunhill test -x /opt/sunhill-nvr/current/app/sunhill_nvr
python3 tests/ubuntu_install_smoke.py "$test_dir/install.log"
