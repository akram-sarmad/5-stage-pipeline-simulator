# 5-Stage RISC-V Pipeline Simulator

![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)
![ISA](https://img.shields.io/badge/ISA-RISC--V%20(RV32I%20subset)-informational)
![Tests](https://img.shields.io/badge/tests-16%2F16%20passing-brightgreen)
![Dependencies](https://img.shields.io/badge/dependencies-none-lightgrey)

A cycle-accurate simulator of the classic 5-stage pipeline (IF, ID, EX, MEM, WB)
for a small RV32I-subset ISA, with data forwarding, load-use stalling, and
control-hazard flushing. Correctness is verified against a non-pipelined
golden reference model on every test program.

**Quick start:** `python3 tests.py` — no dependencies, runs in under a second,
prints a pass/fail table for all 8 test programs against the golden model.

## Why this project

Understanding pipeline hazards at a mechanistic level — not just the textbook
diagram — is core to computer architecture, RTL design, and verification
alike. This simulator implements and demonstrates all three hazard types
(data, load-use, control), with measurable evidence (cycle traces, CPI,
stall/flush counts) rather than just a description, and validates every
result against an independent golden reference model — the same
build-a-checker-you-trust approach used in functional verification.

## Architecture

```
isa.py        Two-pass assembler: assembly text + labels -> Instr objects
refmodel.py   Golden model: executes one instruction fully per step
pipeline.py   The 5-stage pipelined CPU with forwarding/stall/flush logic
tests.py      Test programs; asserts pipeline output == reference model
trace_print.py  Cycle-by-cycle IF/ID/EX/MEM/WB trace printer
```

### ISA subset
`add, sub, and, or, addi, lw, sw, beq, bne, jal, nop, halt` — enough to
exercise every hazard type while staying small enough to reason about fully.

### Pipeline registers
Four registers sit between the five stages (`IFID`, `IDEX`, `EXMEM`, `MEMWB`),
each carrying the in-flight instruction plus whatever data has been computed
so far. Every cycle, every stage computes its new output using only
**old** (start-of-cycle) pipeline-register state; the four registers are then
swapped over together at the end of the cycle. This is what makes "all five
stages happen simultaneously" (true in hardware) work correctly in software,
where statements execute one at a time.

### Hazard handling

| Hazard | Example | Fix |
|---|---|---|
| Data (RAW) | `add x1,x2,x3` then `sub x4,x1,x5` | Forward from `EXMEM.alu_out` (1 instr apart) or `MEMWB.result` (2 instrs apart) directly into EX |
| Load-use | `lw x1,0(x2)` then `add x3,x1,x4` | 1-cycle stall — the loaded value doesn't exist until MEM completes, so there is nothing to forward yet |
| Control | `beq x1,x2,TARGET` | Resolved in EX; if taken, flush the 2 younger instructions already in IF/ID and redirect the PC |

Forwarding and load-use stalling can be toggled off (`forwarding=False`) to
compare CPI with and without hazard mitigation.

## Edge cases explicitly tested

- **Back-to-back taken branches** — confirms a flush can be immediately
  followed by another flush with no interference (`back_to_back_branches`).
- **`x0` as a destination register** — confirms writes to `x0` are discarded
  and never forwarded as garbage into a later instruction (`x0_edge_case`).
- **Store immediately followed by a load to the same address** — verified
  correct, but *not* because of special-case logic: in this 5-stage,
  single-cycle-EX design, a `sw` always reaches MEM at least one full cycle
  before any later `lw` can, so the write always commits before the read.
  This hazard is structurally impossible here; it only becomes a real
  concern in more advanced designs (out-of-order execution, multi-cycle
  memory).
- **Invalid/out-of-bounds branch target** — the simulator does not crash;
  it drains cleanly. `PipelineCPU.run()` sets `self.ran_off_end = True` in
  this case (vs. a normal `self.halted = True` for a proper `halt`), so
  calling code can detect a likely program bug rather than mistaking a
  drain for a clean exit.

## Results

Run `python3 tests.py` (or `py tests.py` on Windows):

```
program            fwd   match   cycles   instrs   CPI    stalls   flushes
raw_chain          True  True    10       5        2.00   0        0
raw_chain          False True    18       5        3.60   8        0

load_use           True  True    11       5        2.20   1        0
load_use           False True    16       5        3.20   6        0

mem_wb_forward     True  True    8        3        2.67   0        0
mem_wb_forward     False True    9        3        3.00   1        0

branch_loop        True  True    30       17       1.76   0        4
branch_loop        False True    42       17       2.47   12       4

branch_not_taken   True  True    10       5        2.00   0        0
branch_not_taken   False True    12       5        2.40   2        0

jump               True  True    9        2        4.50   0        1
jump               False True    9        2        4.50   0        1

back_to_back_branches True  True    14       5        2.80   0        2
back_to_back_branches False True    16       5        3.20   2        2

x0_edge_case       True  True    7        2        3.50   0        0
x0_edge_case       False True    7        2        3.50   0        0

ALL TESTS PASSED
```

Every program's final register/memory state matches the non-pipelined
reference model exactly. Forwarding measurably reduces stalls and CPI across
every hazard-bearing program (e.g. `raw_chain`: CPI 3.60 -> 2.00 once
forwarding is enabled).

`raw_chain`'s CPI (2.00, not closer to 1.0) is expected for a 5-instruction
program: a 5-stage pipeline always pays ~5 cycles of fill/drain overhead
regardless of hazards, which dominates on short programs. On a longer
20-instruction hazard-chain program, CPI is 1.26 — approaching the ideal of 1.

## Sample trace (load-use stall)

`python3 trace_print.py` prints a full cycle-by-cycle table. Excerpt:

```
cyc  IF                   ID                   EX                   MEM                  WB
4    addi x4, x3, 100     lw   x2, 0(x0)       sw   x1, 0(x0)       addi x1, x0, 8       --
5    addi x4, x3, 100     add  x3, x2, x2      lw   x2, 0(x0)       sw   x1, 0(x0)       addi x1, x0, 8   STALL(load-use/RAW)
6    halt                 add  x3, x2, x2      --                   lw   x2, 0(x0)       sw   x1, 0(x0)
```

At cycle 5, `add x3, x2, x2` needs `x2`, which `lw` won't produce until MEM
completes — so ID holds for one cycle (EX gets a bubble at cycle 6) instead
of reading a stale value.

## Running it

Requires only Python 3.8+ (standard library only — no dependencies to install).

```bash
python3 tests.py          # run all test programs, verify vs reference model
python3 trace_print.py    # print cycle-by-cycle traces for two programs
```

On Windows, if `python3` isn't recognized, use `py` instead:
```powershell
py tests.py
py trace_print.py
```

## Possible extensions

- Branch prediction (static taken / 1-bit / 2-bit dynamic) to reduce the
  flush penalty on branch-heavy code, with a misprediction-rate comparison
- Multi-cycle EX (e.g. a `mul` instruction) to explore structural hazards
- A configurable memory latency to explore additional stalling scenarios
