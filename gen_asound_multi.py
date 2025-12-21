#!/usr/bin/env python3
import os
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple, Set


@dataclass
class AlsaCard:
    num: int
    short: str   # e.g. "A", "CODEC"
    desc: str    # e.g. "USB Audio CODEC"
    dev: int = 0


@dataclass
class HwParams:
    access: Set[str]
    formats: Set[str]
    rate_fixed: Optional[int]        # e.g. 48000 if fixed
    rate_range: Optional[Tuple[int, int]]  # (min, max) if range
    channels_fixed: Optional[int]
    channels_range: Optional[Tuple[int, int]]


def run_cmd(cmd: List[str]) -> str:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return p.stdout


def parse_aplay_l(output: str) -> List[AlsaCard]:
    """
    Parse lines like:
    card 3: A [USB-C to 3.5mm Headphone Jack A], device 0: USB Audio [USB Audio]
    """
    cards: List[AlsaCard] = []
    for line in output.splitlines():
        m = re.match(r"card\s+(\d+):\s+(\S+)\s+\[(.*?)\],\s+device\s+(\d+):\s+(.*?)\s+\[(.*?)\]", line)
        if m:
            num = int(m.group(1))
            short = m.group(2)
            desc = m.group(3)
            dev = int(m.group(4))
            # Prefer device 0; we keep it for completeness
            cards.append(AlsaCard(num=num, short=short, desc=desc, dev=dev))
    # Deduplicate by card number; keep first device seen (often dev 0)
    uniq = {}
    for c in cards:
        uniq.setdefault(c.num, c)
    return [uniq[k] for k in sorted(uniq.keys())]


def parse_hw_params(output: str) -> HwParams:
    # ACCESS
    access = set()
    m = re.search(r"ACCESS:\s+(.*)", output)
    if m:
        access = set(m.group(1).split())

    # FORMAT
    formats = set()
    m = re.search(r"FORMAT:\s+(.*)", output)
    if m:
        formats = set(m.group(1).split())

    # CHANNELS
    channels_fixed = None
    channels_range = None
    m = re.search(r"CHANNELS:\s+\[(\d+)\s+(\d+)\]", output)
    if m:
        channels_range = (int(m.group(1)), int(m.group(2)))
    else:
        m2 = re.search(r"CHANNELS:\s+(\d+)", output)
        if m2:
            channels_fixed = int(m2.group(1))

    # RATE
    rate_fixed = None
    rate_range = None
    m = re.search(r"RATE:\s+\[(\d+)\s+(\d+)\]", output)
    if m:
        rate_range = (int(m.group(1)), int(m.group(2)))
    else:
        m2 = re.search(r"RATE:\s+(\d+)", output)
        if m2:
            rate_fixed = int(m2.group(1))

    return HwParams(
        access=access,
        formats=formats,
        rate_fixed=rate_fixed,
        rate_range=rate_range,
        channels_fixed=channels_fixed,
        channels_range=channels_range,
    )


def dump_hw_params(card_short: str, dev: int = 0) -> HwParams:
    # Using /dev/zero is fine; aplay will print HW params then fail, but params dump appears.
    cmd = ["aplay", f"-D", f"hw:CARD={card_short},DEV={dev}", "--dump-hw-params", "-d 1", "/dev/zero"]
    out = run_cmd(cmd)
    print(out)
    return parse_hw_params(out)


def choose_common_rate(p1: HwParams, p2: HwParams) -> Optional[int]:
    """
    Prefer 48000, else 44100, else 32000, else pick any integer in overlap.
    NOTE: Many USB dongles don't support 44100 in hw mode; 48000 is often safest.
    """
    preferred = [48000, 44100, 96000, 32000]

    def supports(p: HwParams, rate: int) -> bool:
        if p.rate_fixed is not None:
            return p.rate_fixed == rate
        if p.rate_range is not None:
            lo, hi = p.rate_range
            return lo <= rate <= hi
        return False

    for r in preferred:
        if supports(p1, r) and supports(p2, r):
            return r

    # If no preferred match, try to pick something in range overlap
    # Fixed vs range / range vs range:
    def get_range(p: HwParams) -> Optional[Tuple[int, int]]:
        if p.rate_fixed is not None:
            return (p.rate_fixed, p.rate_fixed)
        return p.rate_range

    r1 = get_range(p1)
    r2 = get_range(p2)
    if not r1 or not r2:
        return None
    lo = max(r1[0], r2[0])
    hi = min(r1[1], r2[1])
    if lo > hi:
        return None
    # pick lo as a valid candidate
    return lo


def choose_common_format(p1: HwParams, p2: HwParams) -> Optional[str]:
    """
    Prefer S16_LE, else S24_3LE, else S32_LE, else any common format.
    """
    common = p1.formats.intersection(p2.formats)
    if not common:
        return None
    for f in ["S16_LE", "S24_3LE", "S32_LE"]:
        if f in common:
            return f
    return sorted(common)[0]


def generate_asound_conf(cardA: AlsaCard, cardB: AlsaCard, rate: int, fmt: str) -> str:
    # Map ch0-1 -> A, ch2-3 -> B
    return f"""# ============================================================
# Auto-generated ALSA config: virtual 4ch device from two stereo devices
#
# Device A: card {cardA.num} ({cardA.short})  -> hw:CARD={cardA.short},DEV={cardA.dev}
# Device B: card {cardB.num} ({cardB.short})  -> hw:CARD={cardB.short},DEV={cardB.dev}
#
# Mapping:
#   ch0-1 -> Device A (L/R)
#   ch2-3 -> Device B (L/R)
#
# User-facing device: "convert4"
# Pinned common hw params: rate={rate}, format={fmt}, channels=4
# ============================================================

pcm.both {{
  type route;
  slave.pcm {{
    type multi;

    slaves.a.pcm "hw:CARD={cardA.short},DEV={cardA.dev}";
    slaves.a.channels 2;

    slaves.b.pcm "hw:CARD={cardB.short},DEV={cardB.dev}";
    slaves.b.channels 2;

    bindings.0.slave a; bindings.0.channel 0;
    bindings.1.slave a; bindings.1.channel 1;
    bindings.2.slave b; bindings.2.channel 0;
    bindings.3.slave b; bindings.3.channel 1;

    hint {{ description "Combo HW ({cardA.short}+{cardB.short}) raw 4ch" }}
  }}

  ttable.0.0 1;
  ttable.1.1 1;
  ttable.2.2 1;
  ttable.3.3 1;
}}

ctl.both {{
  type hw;
  card {cardA.short};
}}

pcm.convert4 {{
  type plug
  slave {{
    pcm both
    format {fmt}
    channels 4
    rate {rate}
  }}
  hint {{ description "Combo Converted 4ch {fmt} {rate}Hz" }}
}}
"""


def pick_two_cards(cards: List[AlsaCard]) -> Tuple[AlsaCard, AlsaCard]:
    print("\nDetected ALSA playback cards from `aplay -l`:")
    for c in cards:
        print(f"  [{c.num}] short='{c.short}' dev={c.dev}  desc='{c.desc}'")

    def ask(prompt: str) -> int:
        while True:
            s = input(prompt).strip()
            if s.isdigit():
                return int(s)
            print("Please enter a numeric card number.")

    n1 = ask("\nEnter the FIRST card number to combine (e.g., 3): ")
    n2 = ask("Enter the SECOND card number to combine (e.g., 4): ")
    if n1 == n2:
        raise SystemExit("You must choose two different cards.")

    by_num = {c.num: c for c in cards}
    if n1 not in by_num or n2 not in by_num:
        raise SystemExit("One of the card numbers is not in the list.")
    return by_num[n1], by_num[n2]


def main():
    aplay_l = run_cmd(["aplay", "-l"])
    cards = parse_aplay_l(aplay_l)

    if len(cards) < 2:
        raise SystemExit("Need at least 2 playback cards from `aplay -l`.")

    cardA, cardB = pick_two_cards(cards)

    print("\nProbing hardware capabilities (this may print some ALSA warnings)...")
    pA = dump_hw_params(cardA.short, cardA.dev)
    pB = dump_hw_params(cardB.short, cardB.dev)

    rate = choose_common_rate(pA, pB)
    fmt = choose_common_format(pA, pB)

    print("\n=== Hardware summary ===")
    print(f"Card {cardA.num} ({cardA.short}): formats={sorted(pA.formats)} rate_fixed={pA.rate_fixed} rate_range={pA.rate_range}")
    print(f"Card {cardB.num} ({cardB.short}): formats={sorted(pB.formats)} rate_fixed={pB.rate_fixed} rate_range={pB.rate_range}")
    print("\n=== Selected common params ===")
    print(f"Common rate:   {rate}")
    print(f"Common format: {fmt}")

    if rate is None or fmt is None:
        raise SystemExit("No common rate/format found in hw params. Try different devices or use plughw-only (no multi).")

    conf = generate_asound_conf(cardA, cardB, rate, fmt)

    out_path = "/etc/asound.conf"
    out_preview = "./asound.conf.generated"
    with open(out_preview, "w", encoding="utf-8") as f:
        f.write(conf)

    print(f"\nGenerated config written to: {out_preview}")
    print("To install it system-wide, run:")
    print(f"  sudo cp {out_preview} {out_path}")
    print("Then reboot (recommended):")
    print("  sudo reboot")
    print("\nAfter reboot, test:")
    print(f"  aplay -L | grep convert4")
    print(f"  speaker-test -D convert4 -c 4 -r {rate} -f {fmt} -t sine")


if __name__ == "__main__":
    main()

