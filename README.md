# Raspberry Pi: USB Gadget Audio In -> Dual USB DAC Out (Main + Sub) with CamillaDSP

This repo configures a Raspberry Pi as a **USB Audio Class 2 (UAC2) gadget** (capture device) to receive audio from a host over USB, then uses **CamillaDSP** to process and split that audio and play it out through **two separate USB DACs**:

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
        +--------------------------------------------------+
        |                    CamillaDSP                    |
        | /home/xxie/camilladsp/configs/active_config.yml |
        +--------------------------------------------------+
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
- `dsp.yml.example`: Example CamillaDSP pipeline/config. Copy it into `/home/xxie/camilladsp/configs/active_config.yml` (or another underscore-only filename) and let the statefile select it.
- `mains_only.yml`: Stereo mains without subwoofer (same PEQ stack, crossover removed). Copy into `/home/xxie/camilladsp/configs/`.
- `bypass.yml`: Pure passthrough, no EQ and no crossover (A/B reference). Copy into `/home/xxie/camilladsp/configs/`.
- `dsp-switch`: CLI to swap the live config instantly via websocket (`SetConfigFilePath` + `Reload`), no restart. Install with `sudo install -m 0755 dsp-switch /usr/local/bin/dsp-switch`, then `dsp-switch status|with-sub|mains-only`.
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
- `UAC2_C_SRATE`: Must match what your audio pipeline expects (example `/home/xxie/camilladsp/configs/active_config.yml` uses `48000`).
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

### 5) Configure CamillaDSP to use the web UI managed config

Use the standard CamillaDSP plus CamillaGUI layout: systemd starts CamillaDSP with the statefile and wait mode, while the active config lives under `/home/xxie/camilladsp/configs/`.

First, install the example config into the GUI config directory and adjust it:

```bash
install -d /home/xxie/camilladsp/configs
cp ./dsp.yml.example /home/xxie/camilladsp/configs/active_config.yml
vi /home/xxie/camilladsp/configs/active_config.yml
```

Then make sure `/home/xxie/camilladsp/statefile.yml` points at that file:

```yaml
config_path: /home/xxie/camilladsp/configs/active_config.yml
```

1. Create/edit the drop-in:

```bash
sudo SYSTEMD_EDITOR=vi systemctl edit camilladsp.service
```

2. Add this override:

```ini
[Unit]
After=usb-gadget-uac2.service
Requires=usb-gadget-uac2.service

[Service]
WorkingDirectory=/home/xxie
ExecStart=
ExecStart=camilladsp -s camilladsp/statefile.yml -w -g-40 -o camilladsp/camilladsp.log -p 1234 -a 0.0.0.0
```

3. Reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart camilladsp.service
systemctl status --no-pager -l camilladsp.service
```

Notes:
- The web UI only persists configs inside `/home/xxie/camilladsp/configs`.
- Use underscore-only config filenames such as `active_config.yml`; avoid spaces in names.
- With `-w`, CamillaDSP reads the selected config path from `/home/xxie/camilladsp/statefile.yml` instead of taking a positional config file argument.
- If your service runs as a different user than `xxie`, update `WorkingDirectory=` and the paths accordingly.

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
- In `/home/xxie/camilladsp/configs/active_config.yml`, set playback device to `convert4` (already shown in this repo).

## Main/Sub Routing in CamillaDSP

The usual channel convention with `convert4` is:
- Channels `0-1` -> USB DAC A (main L/R)
- Channels `2-3` -> USB DAC B (sub; often mono duplicated to L/R)

In `/home/xxie/camilladsp/configs/active_config.yml` you typically:
- Apply a high-pass to the main channels (0-1).
- Create a mono sum from L/R, apply a low-pass, then duplicate it to channels 2 and 3.

## Switching configs quickly (`dsp-switch`)

The live config is selected by `/home/xxie/camilladsp/statefile.yml` (`config_path:`)
from files in `/home/xxie/camilladsp/configs/`. (`/etc/camilladsp/config.yml` is a
stale leftover and is not used — the service runs with `-s camilladsp/statefile.yml -w`.)

```bash
dsp-switch status       # show active config + processing state
dsp-switch with-sub     # active_config.yml (mains + subwoofer)
dsp-switch mains-only   # mains_only.yml (stereo mains, sub silent)
dsp-switch bypass       # bypass.yml (no EQ, no crossover)
```

The switch goes over the websocket API (`SetConfigFilePath` + `Reload` on port
1234), so it applies instantly without restarting the service, and the statefile
is updated so the choice survives reboots. If the websocket is unreachable it
falls back to `systemctl restart camilladsp`.

## Common Pitfalls / Debugging

### CamillaDSP doesn’t stay running after reboot

Check logs:

```bash
journalctl -b -u camilladsp.service --no-pager -n 200
```

Two common causes:

1) Bad `ExecStart` override
- Symptom: usage errors or `--check`/`-c` complaints.
- Fix: in the drop-in, clear the old start line with `ExecStart=` and start CamillaDSP with `-s camilladsp/statefile.yml -w`.

2) Sample-rate mismatch (very common)
- Symptom: ALSA error like `snd_pcm_hw_params_set_rate ... Invalid argument (22)` then CamillaDSP exits.
- Fix: ensure these all match:
  - Gadget capture rate: `UAC2_C_SRATE` in `/etc/usb-gadget-uac2.conf`
  - CamillaDSP rate: `devices.samplerate` in `/home/xxie/camilladsp/configs/active_config.yml`
  - `convert4` rate: the pinned `rate` in `/etc/asound.conf` (generated by `gen_asound_multi.py`)
  - Example: if you want `48000`, set `UAC2_C_SRATE=48000`, ensure `/home/xxie/camilladsp/configs/active_config.yml` uses `48000`, regenerate `/etc/asound.conf` if needed, then:
    ```bash
    sudo systemctl restart usb-gadget-uac2.service
    sudo systemctl restart camilladsp.service
    ```

### Web UI changes do not persist

If CamillaGUI logs an error like this, your service is still using the wrong config path:

```text
The config file path /etc/camilladsp/config.yml is not in the config dir /home/xxie/camilladsp/configs
```

Fix the service override so CamillaDSP starts with `-w` and let `/home/xxie/camilladsp/statefile.yml` point to a file inside `/home/xxie/camilladsp/configs/`.

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
3. Update `/etc/usb-gadget-uac2.conf` and your active CamillaDSP config under `/home/xxie/camilladsp/configs/`.
4. Reboot, then verify:
   - `systemctl status usb-gadget-uac2.service camilladsp.service`
   - `cat /sys/kernel/config/usb_gadget/uac2g/UDC`
