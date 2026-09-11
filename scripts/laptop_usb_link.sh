#!/usr/bin/env bash
# Bring up the laptop end of the USB EO link. RUN THIS ON THE LAPTOP.
# Finds the interface the Pi presents as a USB Ethernet gadget and pins
# 10.55.0.2 on it, so the receiver can reach the Pi at 10.55.0.1.
set -euo pipefail

PI_IP=10.55.0.1
LAPTOP_IP=10.55.0.2
PREFIX=29
CON_NAME=aura-eo

echo "==> looking for a USB Ethernet gadget interface"
IFACE=""
for path in /sys/class/net/*; do
  dev=$(basename "$path")
  [[ "$dev" == lo ]] && continue
  # cdc_ether / cdc_ncm / rndis_host are what g_ether shows up as on the host.
  drv=$(basename "$(readlink -f "$path/device/driver" 2>/dev/null || echo none)")
  case "$drv" in
    cdc_ether|cdc_ncm|cdc_subset|rndis_host|usbnet)
      IFACE="$dev"; echo "    found $dev (driver $drv)"; break ;;
  esac
done

if [[ -z "$IFACE" ]]; then
  cat >&2 <<'MSG'
ERROR: no USB Ethernet gadget interface found.

Check, in order:
  1. Is the Pi booted and the cable in the Pi's DATA port?
       Pi Zero: the port labelled USB, NOT PWR -- the PWR port has no data lines.
       Pi 4/5:  the USB-C port.
  2. Is the cable a data cable? Many USB cables sold for charging have only
     power conductors and will boot the Pi while carrying no data at all.
     This is the single most common cause of this message.
  3. Did you reboot the Pi after running setup_pi_gadget.sh?
  4. dmesg | tail -20   -- look for 'cdc_ether' or 'RNDIS'
MSG
  exit 1
fi

echo "==> assigning $LAPTOP_IP/$PREFIX to $IFACE"
if command -v nmcli >/dev/null 2>&1 && nmcli -t general status >/dev/null 2>&1; then
  # NetworkManager owns interfaces on Pop!_OS; a raw `ip addr add` gets reverted
  # the moment NM notices the carrier. Give it a profile instead.
  nmcli connection delete "$CON_NAME" >/dev/null 2>&1 || true
  nmcli connection add type ethernet ifname "$IFACE" con-name "$CON_NAME" \
      ipv4.method manual ipv4.addresses "$LAPTOP_IP/$PREFIX" \
      ipv4.never-default yes ipv6.method disabled >/dev/null
  nmcli connection up "$CON_NAME" >/dev/null
  echo "    NetworkManager profile '$CON_NAME' active"
else
  sudo ip addr replace "$LAPTOP_IP/$PREFIX" dev "$IFACE"
  sudo ip link set "$IFACE" up
  echo "    static address set directly (no NetworkManager)"
fi

echo "==> pinging the Pi"
if ping -c 3 -W 2 "$PI_IP" >/dev/null 2>&1; then
  echo "    link up: $LAPTOP_IP <-> $PI_IP"
else
  echo "    NO REPLY from $PI_IP." >&2
  echo "    On the Pi run: ip addr show usb0   (expect $PI_IP)" >&2
  echo "    If usb0 exists but has no address: systemctl status aura-usb0" >&2
  exit 1
fi

echo
echo "Start the stream:"
echo "  Pi:      python3 -m aura.eo.sender --device /dev/video0 --size 1280x720 --fps 30"
echo "  Laptop:  python3 -m aura.eo.receiver --host $PI_IP --show"
echo
echo "Remove:  nmcli connection delete $CON_NAME"
