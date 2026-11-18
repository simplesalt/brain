[ -n "${BSB_SHELL_SOURCED:-}" ] && return 0
BSB_SHELL_SOURCED=1

[ -n "${CONTAINER_ID:-}" ] || return 0
command -v distrobox-host-exec >/dev/null 2>&1 || return 0

BSB_UNIT="builder-sb@${CONTAINER_ID}.service"
export BSB_UNIT

bsb_host() {
    DBX_NON_INTERACTIVE=1 distrobox-host-exec "$@"
}

bsb_status()  { bsb_host systemctl --user status "${BSB_UNIT}"; }
bsb_enable()  { bsb_host systemctl --user enable --now "${BSB_UNIT}"; }
bsb_disable() { bsb_host systemctl --user disable --now "${BSB_UNIT}"; }
bsb_restart() { bsb_host systemctl --user restart "${BSB_UNIT}"; }
bsb_logs()    { bsb_host journalctl --user -u "${BSB_UNIT}" -n 50 --no-pager; }

if [ -n "${BASH_VERSION:-}" ]; then
    eval 'bsb-status()  { bsb_status; }
bsb-enable()  { bsb_enable; }
bsb-disable() { bsb_disable; }
bsb-restart() { bsb_restart; }
bsb-logs()    { bsb_logs; }'
else
    alias bsb-status='bsb_status'
    alias bsb-enable='bsb_enable'
    alias bsb-disable='bsb_disable'
    alias bsb-restart='bsb_restart'
    alias bsb-logs='bsb_logs'
fi
