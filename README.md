# Raspberry Pi: USB Gadget Audio In → Dual USB DAC Out (Main + Sub) with CamillaDSP

This repo configures a Raspberry Pi as a **USB Audio Class 2 (UAC2) gadget** (capture device) to receive audio from a host over USB, then uses **CamillaDSP** to process/split that audio and play it out through **two separate USB DACs**:

- USB DAC A: main speakers (stereo)
- USB DAC B: subwoofer (typically mono duplicated to L/R)

```text
          (USB audio from host PC / phone)
                      |
                      v
        +-----------------------------+
        | Raspberry Pi (UAC2 gadget)  |
        |  Capture: hw:CARD=UAC2Gadget|
        +-----------------------------+
                      |
                      v
        +-----------------------------+
        |         CamillaDSP          |
        |  /etc/camilladsp/config.yml |
        |  split: mains + sub         |
        +-----------------------------+
                      |
                      v
        +-----------------------------+
        |   ALSA virtual device       |
        |   "convert4" (4 channels)   |
        |   from /etc/asound.conf     |
        +-----------------------------+
               |                 |
          ch 0-1 (mains)    ch 2-3 (sub)
               |                 |
               v                 v
        +-------------+   +-------------+
        | USB DAC A   |   | USB DAC B   |
        | Main L/R    |   | Sub (L/R)   |
        +-------------+   +-------------+
```

Tested on Raspberry Pi OS (Debian-based) with `libcomposite` + `configfs`.

## What’s Included

- `usb_gadget.sh`: Creates/configures the UAC2 gadget via `configfs` and binds it to a UDC.
- `usb-gadget-uac2.conf.example`: Example overrides for the gadget (sample rate/format/channel mask, etc).
- `systemd/usb-gadget-uac2.service`: Systemd oneshot unit template to configure the gadget at boot.
- `dsp.yml.example`: Example CamillaDSP pipeline/config (install to `/etc/camilladsp/config.yml` and customize).
- `gen_asound_multi.py`: Generates an ALSA virtual output device by combining two stereo USB DACs into one 4-channel device (`convert4`) for CamillaDSP.

## Prerequisites

- Raspberry Pi with USB gadget-capable controller (typical on Pi Zero / Pi 4/5 USB-C gadget port setups).
- Two USB DACs (one for mains, one for sub).
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
- `UAC2_C_SRATE`: Must match what your audio pipeline expects (example `/etc/camilladsp/config.yml` uses `48000`).
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

### 4) Create the 2-DAC playback device (`convert4`)

Generate and install `/etc/asound.conf` so CamillaDSP can play to two USB DACs as one 4-channel device:

```bash
./gen_asound_multi.py
sudo cp ./asound.conf.generated /etc/asound.conf
sudo reboot
```

After reboot:

```bash
aplay -L | grep convert4
```

### 5) Configure CamillaDSP to use your config and wait for the gadget

This is done with a systemd drop-in override for `camilladsp.service`.

First, install the example config to a system-wide location and adjust it (recommended):

```bash
sudo install -d /etc/camilladsp
sudo install -m 0644 ./dsp.yml.example /etc/camilladsp/config.yml
sudo vi /etc/camilladsp/config.yml
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
ExecStart=camilladsp -s camilladsp/statefile.yml -g-40 -o camilladsp/camilladsp.log -p 1234 /etc/camilladsp/config.yml
```

3. Reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart camilladsp.service
systemctl status --no-pager -l camilladsp.service
```

Notes:
- Do **not** use `~` in systemd paths; use absolute paths like `/etc/camilladsp/config.yml`.
- Do **not** add `-c` unless you want “check config and exit”. The config file is the positional `CONFIGFILE` argument.
 - If your service runs as a different user than `xxie`, update `WorkingDirectory=` and the config path accordingly.

## Create the 2-DAC playback device (`convert4`)

If you already did **Quick Start step 4**, you can skip this section; it explains what the generated ALSA config does.

To feed **two separate USB DACs** from one application (CamillaDSP), create a single 4-channel ALSA playback device. `gen_asound_multi.py` generates an `/etc/asound.conf` that exposes:

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
- In `/etc/camilladsp/config.yml`, set playback device to `convert4` (already shown in this repo).

## Main/Sub Routing in CamillaDSP

The usual channel convention with `convert4` is:
- Channels `0-1` → USB DAC A (main L/R)
- Channels `2-3` → USB DAC B (sub; often mono duplicated to L/R)

In `/etc/camilladsp/config.yml` you typically:
- Apply a high-pass to the main channels (0-1).
- Create a mono sum from L/R, apply a low-pass, then duplicate it to channels 2 and 3.

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
- Fix: ensure these all match:
  - Gadget capture rate: `UAC2_C_SRATE` in `/etc/usb-gadget-uac2.conf`
  - CamillaDSP rate: `devices.samplerate` in `/etc/camilladsp/config.yml`
  - `convert4` rate: the pinned `rate` in `/etc/asound.conf` (generated by `gen_asound_multi.py`)
  - Example: if you want `48000`, set `UAC2_C_SRATE=48000`, ensure `/etc/camilladsp/config.yml` uses `48000`, regenerate `/etc/asound.conf` if needed, then:
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
