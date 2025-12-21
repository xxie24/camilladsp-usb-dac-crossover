# Raspberry Pi USB Gadget Audio (UAC2) + CamillaDSP

This repo configures a Raspberry Pi as a **USB Audio Class 2 (UAC2) gadget** (capture device) and then starts **CamillaDSP** after the gadget is ready.

Tested on Raspberry Pi OS (Debian-based) with `libcomposite` + `configfs`.

## What’s Included

- `usb_gadget.sh`: Creates/configures the UAC2 gadget via `configfs` and binds it to a UDC.
- `usb-gadget-uac2.conf.example`: Example overrides for the gadget (sample rate/format/channel mask, etc).
- `systemd/usb-gadget-uac2.service`: Systemd oneshot unit template to configure the gadget at boot.
- `dsp.yml.example`: Example CamillaDSP pipeline/config (copy to `dsp.yml` and customize).
- `gen_asound_multi.py`: Generates an ALSA virtual output device by combining two stereo USB DACs into one 4-channel device (optional).

## Prerequisites

- Raspberry Pi with USB gadget-capable controller (typical on Pi Zero / Pi 4/5 USB-C gadget port setups).
- Kernel modules available: `libcomposite` (and `configfs` mounted at `/sys/kernel/config`).
- `camilladsp` installed and a working `camilladsp.service` (or you will create one).
- For `gen_asound_multi.py`: `python3` and `alsa-utils` (`aplay`).

## (Optional) Ensure `libcomposite` loads at boot

On Debian/Raspberry Pi OS:

```bash
echo libcomposite | sudo tee /etc/modules-load.d/usb-gadget.conf
```

## Quick Start (Step-by-Step)

### 1) Install the gadget script

Install to a stable path (do not run from your home directory in a unit file):

```bash
sudo install -m 0755 ./usb_gadget.sh /usr/local/sbin/usb_gadget.sh
```

### 2) Install the gadget config file

The script reads `/etc/usb-gadget-uac2.conf` if it exists (it is `source`d by bash).

```bash
sudo install -m 0644 ./usb-gadget-uac2.conf.example /etc/usb-gadget-uac2.conf
sudo vi /etc/usb-gadget-uac2.conf
```

Important settings:
- `UAC2_C_SRATE`: Must match what your audio pipeline expects (example `dsp.yml` uses `48000`).
- `UAC2_C_SSIZE`: Sample size in bytes (`2`=16-bit, `3`=24-bit packed, `4`=32-bit).
- `UAC2_C_CHMASK`: Channel mask (`0x3` = stereo).
- `UAC2_UDC` (optional): Pin the UDC name instead of “first found”.

### 3) Install and enable the gadget systemd service

Install the unit template (it expects `usb_gadget.sh` at `/usr/local/sbin/usb_gadget.sh`).

```bash
sudo install -m 0644 ./systemd/usb-gadget-uac2.service /etc/systemd/system/usb-gadget-uac2.service
sudo systemctl daemon-reload
sudo systemctl enable --now usb-gadget-uac2.service
```

Verify it configured the gadget:

```bash
systemctl status --no-pager -l usb-gadget-uac2.service
cat /sys/kernel/config/usb_gadget/uac2g/UDC
```

### 4) Configure CamillaDSP to use your config and wait for the gadget

This is done with a systemd drop-in override for `camilladsp.service`.

First, copy the example config and adjust it:

```bash
cp ./dsp.yml.example ./dsp.yml
vi ./dsp.yml
```

1. Create/edit the drop-in:

```bash
sudo SYSTEMD_EDITOR=vi systemctl edit camilladsp.service
```

2. Add this (update the config path if needed):

```ini
[Unit]
After=usb-gadget-uac2.service
Requires=usb-gadget-uac2.service

[Service]
WorkingDirectory=/home/xxie
ExecStart=
ExecStart=camilladsp -s camilladsp/statefile.yml -g-40 -o camilladsp/camilladsp.log -p 1234 /home/xxie/src/dsp.yml
```

3. Reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart camilladsp.service
systemctl status --no-pager -l camilladsp.service
```

Notes:
- Do **not** use `~` in systemd paths; use absolute paths like `/home/xxie/src/dsp.yml`.
- Do **not** add `-c` unless you want “check config and exit”. The config file is the positional `CONFIGFILE` argument.
 - If your service runs as a different user than `xxie`, update `WorkingDirectory=` and the config path accordingly.

## Optional: Combine 2 USB DACs into 1 virtual ALSA device (`convert4`)

If you have two separate stereo USB DACs but want a single 4-channel ALSA playback device (e.g. for CamillaDSP), `gen_asound_multi.py` generates an `/etc/asound.conf` that exposes:

- `pcm.both`: raw 4ch “multi” device (two stereo slaves)
- `pcm.convert4`: a `plug` wrapper that pins a common `rate` + `format` and is easy to use

Steps:

```bash
./gen_asound_multi.py
sudo cp ./asound.conf.generated /etc/asound.conf
sudo reboot
```

After reboot:

```bash
aplay -L | grep convert4
speaker-test -D convert4 -c 4 -t sine
```

How it works:
- It lists playback devices from `aplay -l`, asks you to pick two card numbers, then probes each with `aplay --dump-hw-params`.
- It chooses a common sample rate/format (prefers `48000`), then maps channels `0-1` to DAC A and `2-3` to DAC B.
- In `dsp.yml`, set playback device to `convert4` (already shown in this repo).

## Common Pitfalls / Debugging

### CamillaDSP doesn’t stay running after reboot

Check logs:

```bash
journalctl -b -u camilladsp.service --no-pager -n 200
```

Two common causes:

1) Bad `ExecStart` override
- Symptom: usage errors or `--check`/`-c` complaints.
- Fix: in the drop-in, clear the old start line with `ExecStart=` and set a single correct `ExecStart=... CONFIGFILE`.

2) Sample-rate mismatch (very common)
- Symptom: ALSA error like `snd_pcm_hw_params_set_rate ... Invalid argument (22)` then CamillaDSP exits.
- Fix: ensure **gadget** `UAC2_C_SRATE` matches **CamillaDSP** `devices.samplerate`.
  - Example: if `dsp.yml` uses `48000`, set `UAC2_C_SRATE=48000` in `/etc/usb-gadget-uac2.conf`, then:
    ```bash
    sudo systemctl restart usb-gadget-uac2.service
    sudo systemctl restart camilladsp.service
    ```

### Gadget service fails

```bash
systemctl status --no-pager -l usb-gadget-uac2.service
journalctl -b -u usb-gadget-uac2.service --no-pager -n 200
ls /sys/class/udc
```

If `/sys/class/udc` is empty, the kernel doesn’t see a UDC (common if using the wrong port/cable or missing overlays).

## Reusing on a 2nd Raspberry Pi

On the new Pi:
1. Copy this repo.
2. Repeat the **Quick Start** steps.
3. Update `/etc/usb-gadget-uac2.conf` and your CamillaDSP config path.
4. Reboot, then verify:
   - `systemctl status usb-gadget-uac2.service camilladsp.service`
   - `cat /sys/kernel/config/usb_gadget/uac2g/UDC`
