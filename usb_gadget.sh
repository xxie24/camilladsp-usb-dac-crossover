#!/usr/bin/env bash
set -euo pipefail

NAME=uac2g
FUNC=uac2.usb0
ROOT=/sys/kernel/config/usb_gadget
G="$ROOT/$NAME"
CONFIG_FILE=/etc/usb-gadget-uac2.conf

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  exec sudo -E "$0" "$@"
fi

usage() {
  cat <<'EOF'
Usage: usb_gadget.sh [OPTIONS]

Options:
  --config FILE       Load config overrides from FILE (default: /etc/usb-gadget-uac2.conf)
  --stop_camilladsp   Stop camilladsp via systemd
  --unbind_usb        Unbind gadget UDC and unlink UAC2 function
  --reconfig_usb      Reconfigure gadget (also binds to UDC)
  --start_camilladsp  Start camilladsp via systemd
  -h, --help          Show this help

Default (no options): run all four steps in order.
EOF
}

STOP_CAMILLADSP=0
UNBIND_USB=0
RECONFIG_USB=0
START_CAMILLADSP=0

parse_args() {
  if [ "$#" -eq 0 ]; then
    STOP_CAMILLADSP=1
    UNBIND_USB=1
    RECONFIG_USB=1
    START_CAMILLADSP=1
    return
  fi

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --config)
        CONFIG_FILE="${2:-}"
        if [[ -z "$CONFIG_FILE" ]]; then
          echo "--config requires a FILE argument" >&2
          exit 2
        fi
        shift
        ;;
      --stop_camilladsp) STOP_CAMILLADSP=1 ;;
      --unbind_usb) UNBIND_USB=1 ;;
      --reconfig_usb) RECONFIG_USB=1 ;;
      --start_camilladsp) START_CAMILLADSP=1 ;;
      -h|--help) usage; exit 0 ;;
      *)
        echo "unknown option: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
    shift
  done
}

load_config() {
  if [[ -n "${CONFIG_FILE:-}" && -f "$CONFIG_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
  fi
}

stop_camilladsp() {
  echo "stopping camilladsp (if running)"
  systemctl stop camilladsp >/dev/null 2>&1 || true
}

unbind_usb() {
  if [ -f "$G/UDC" ]; then
    echo "unbinding gadget"
    echo "" >"$G/UDC" || true
    sync
    echo -n "UDC(after unbind)="; cat "$G/UDC" || true
  fi

  if [ -e "$G/configs/c.1/$FUNC" ]; then
    echo "removing existing function link: $G/configs/c.1/$FUNC"
    rm -f "$G/configs/c.1/$FUNC"
  fi
}

start_camilladsp() {
  echo "starting camilladsp"
  systemctl start camilladsp >/dev/null 2>&1 || true
}

stop_device_users() {
  stop_camilladsp
  unbind_usb
}

restart_device_users() {
  start_camilladsp
}

configure_gadget() {
  echo "configuring USB gadget capture-only profile"
  modprobe libcomposite >/dev/null 2>&1 || true

  if ! mountpoint -q /sys/kernel/config; then
    mount -t configfs none /sys/kernel/config
  fi

  cd "$ROOT"
  if [ -d "$NAME" ]; then
    if [ -f "$NAME/UDC" ]; then
      echo "" >"$NAME/UDC" || true
      sync
      echo -n "UDC(after unbind)="; cat "$NAME/UDC" || true
    fi
  else
    mkdir -p "$NAME"
  fi

  cd "$NAME"
  echo "${UAC2_ID_VENDOR:-0x1d6b}" >idVendor
  echo "${UAC2_ID_PRODUCT:-0x0104}" >idProduct
  echo "${UAC2_BCD_USB:-0x0200}" >bcdUSB
  echo "${UAC2_BCD_DEVICE:-0x0100}" >bcdDevice
  echo 0xEF >bDeviceClass
  echo 0x02 >bDeviceSubClass
  echo 0x01 >bDeviceProtocol

  mkdir -p strings/0x409
  echo "${UAC2_MANUFACTURER:-Raspberry Pi}" >strings/0x409/manufacturer
  echo "${UAC2_PRODUCT:-RPi5 UAC2 Capture}" >strings/0x409/product
  echo "${UAC2_SERIAL:-1234567890}" >strings/0x409/serialnumber

  mkdir -p "functions/$FUNC"
  echo "${UAC2_C_CHMASK:-0x3}" >"functions/$FUNC/c_chmask"
  echo "${UAC2_C_SRATE:-44100}" >"functions/$FUNC/c_srate"
  echo "${UAC2_C_SSIZE:-4}" >"functions/$FUNC/c_ssize"
  echo "${UAC2_C_VOLUME_PRESENT:-1}" >"functions/$FUNC/c_volume_present"
  echo "${UAC2_C_VOLUME_MIN:--15360}" >"functions/$FUNC/c_volume_min" # -60dB
  echo "${UAC2_C_VOLUME_MAX:-0}" >"functions/$FUNC/c_volume_max" # 0dB
  echo "${UAC2_C_VOLUME_RES:-128}" >"functions/$FUNC/c_volume_res"
  echo "0x0603" >"functions/$FUNC/c_terminal_type"
  echo "${UAC2_C_CHMASK:-0x0}" >"functions/$FUNC/c_chmask"
  echo "0x0301" >"functions/$FUNC/p_terminal_type"

  echo "${UAC2_P_CHMASK:-0x0}" >"functions/$FUNC/p_chmask"

  mkdir -p configs/c.1/strings/0x409
  echo "${UAC2_CONFIGURATION:-UAC2 Capture}" >configs/c.1/strings/0x409/configuration
  echo "${UAC2_MAXPOWER:-1000}" >configs/c.1/MaxPower

  rm -f configs/c.1/"$FUNC"
  ls -l configs/c.1
  ln -s "functions/$FUNC" configs/c.1/

  local UDC
  UDC="${UAC2_UDC:-}"
  if [[ -z "$UDC" ]]; then
    UDC="$(ls /sys/class/udc 2>/dev/null | head -n1 || true)"
  fi
  if [[ -z "$UDC" ]]; then
    echo "no UDC found under /sys/class/udc; cannot bind gadget" >&2
    exit 1
  fi
  echo "$UDC" >UDC

  echo "capture params:"
  echo -n "  c_chmask="; cat "functions/$FUNC/c_chmask"
  echo -n "  c_srate=";  cat "functions/$FUNC/c_srate"
  echo -n "  c_ssize=";  cat "functions/$FUNC/c_ssize"
  echo "bound to UDC: $UDC"
}

parse_args "$@"
load_config

if ((STOP_CAMILLADSP)); then
  stop_camilladsp
fi
if ((UNBIND_USB)); then
  unbind_usb
fi
if ((RECONFIG_USB)); then
  configure_gadget
fi
if ((START_CAMILLADSP)); then
  start_camilladsp
fi
