<!--
  KinematiK — Formula SAE / Formula EV full-car pre-validation platform
  Created by Frederik Thio. Copyright (c) 2026 Frederik Thio.
  Open source. Original author: Frederik Thio, creator of KinematiK.
-->

# 🩺 PCB Doctor — usage

**Electronics (PCB) tab ▸ 🩺 PCB Doctor.** The board-check panel above it
screens the traces you *declare*; the Doctor screens the board you already
*routed*. It exists for the two reports every team files eventually:

> "The board passed DRC and simulated fine, then it failed on the car."
> "A component died even though the design was theoretically OK."

DRC checks geometry against rules. The Doctor checks copper against **physics**
and against the car's own **integration ledger** — the declared peak currents
that the LV/HV check already uses.

## The 60-second loop

1. **Drop the `.kicad_pcb` in** (KiCad 5–9), or click **Load demo board** to
   see the whole loop on a small ECU board with three planted failures.
2. **Skim the current table.** Every routed net is auto-assigned a current:
   name-matched nets take the owning subsystem's declared peak from the
   ledger (`FAN_PWR` → cooling's peak amps), signal nets get 50 mA, everything
   else 1 A — and every guess says so in *source*. Edit any number; the whole
   diagnosis follows it.
3. **Read the diagnosis.** Each finding says what's wrong, **why it fails on
   the car even though it simulated fine**, which **component or net** is
   implicated, and the exact numeric fix.
4. **Click re-trace.** Under-sized power segments are rewritten *in the
   original file* — only the `(width …)` tokens change, everything else is
   byte-identical, so the patched board reopens in KiCad with the routing
   intact. Download the patched `.kicad_pcb` plus a hand-off fix report, and
   read the before/after FAIL count the Doctor computed by re-diagnosing the
   patched geometry.

## What it catches (that DRC doesn't)

| Failure on the car | What the Doctor computes |
|---|---|
| Trace runs hot / burns | IPC-2221 heating at the **bottleneck segment** of every net, per layer (inner copper cools ~half as well) |
| Trace opens like fuse wire | Onderdonk fusing current vs the board's fuse safety factor |
| Wide trace, dead board anyway | **Via ampacity** — the ⌀0.3 mm barrel choking a 1 mm trace at a layer change; prescribes the stitch count |
| ECU resets under load | True IR drop by **nodal analysis of the actual routed copper mesh** (segments + via barrels as a resistor network), worst pad-to-pad, vs the brown-out threshold from ⚙️ Board context |
| Board dead on arrival | **Copper opens** — pads on one net with no trace/via path between them (the rats-nest line everyone missed); nets with pours are never falsely flagged |
| Arced at the wet event | **HV clearance** vs IPC-2221 table B4 for every net you mark >60 V |
| CAN drops frames at full throttle | Diff-pair **length skew** and width steps, plus HV aggressors running parallel to the pair on real geometry |
| "A bad cap" / "the fuse just blew" | **Component derating**: electrolytics parked on hot copper (life halves per +10 °C), fuses whose marked rating is below their net's real current, connector pins asked to carry more than their family rating |

## 📏 Trace Prescriber (no board file needed)

The multi-layer routing answer sheet, for boards that don't exist yet: enter a
current, a temperature-rise budget, a run length and a via drill, and read off
the minimum width for **0.5 / 1 / 2 oz copper on outer and inner layers**, the
IR drop and heat each implies, and **how many vias every layer change needs**
so the barrel doesn't become the fuse.

## What it will never do

Analytic screening only — the same non-goal the rest of KinematiK keeps. It is
not a field solver (coupled-noise volts and eye diagrams are reported as *not
computed*, never invented), not a DRC replacement (a widened trace can newly
crowd an LV neighbour — the Doctor re-checks HV clearance on the patched
geometry, but re-run KiCad DRC before ordering), and not an autorouter that
invents new copper: it re-sizes the routes *you* made and prescribes the rest.
Diff-pair members are deliberately never auto-widened — width sets impedance —
they get a prescription instead. Validate the patched board in KiCad and your
fab's rules before manufacturing.
