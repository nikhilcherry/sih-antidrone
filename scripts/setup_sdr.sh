#!/usr/bin/env bash
# Day-1 SDR bring-up. Run once, then replug the dongle.
set -euo pipefail

echo "==> installing rtl-sdr userspace tools"
sudo apt-get update -qq
sudo apt-get install -y rtl-sdr librtlsdr-dev soapysdr-tools

echo "==> blacklisting the DVB-T kernel driver (it steals the device)"
sudo tee /etc/modprobe.d/blacklist-rtl.conf >/dev/null <<'BL'
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
BL
sudo modprobe -r dvb_usb_rtl28xxu 2>/dev/null || true

echo "==> udev rule so you do not need sudo"
sudo tee /etc/udev/rules.d/20-rtlsdr.rules >/dev/null <<'UD'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666", GROUP="plugdev"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", MODE="0666", GROUP="plugdev"
UD
sudo udevadm control --reload-rules && sudo udevadm trigger

echo
echo "Now UNPLUG and REPLUG the dongle, then verify:"
echo "  rtl_test -t"
echo "  rtl_power -f 2400M:2500M:1M -g 40 -i 5 -1 sweep.csv"
