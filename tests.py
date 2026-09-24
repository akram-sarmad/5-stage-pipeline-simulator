"""Test harness: runs each program through the pipeline (fwd on/off) and the
reference model, asserts final architectural state matches, and reports stats.
"""
from isa import assemble
from refmodel import RefCPU
from pipeline import PipelineCPU

PROGRAMS = {}

# 1) Straight-line RAW chain: every instruction depends on the previous one.
#    Exercises EX/MEM forwarding back-to-back.
PROGRAMS["raw_chain"] = """
    addi x1, x0, 5
    addi x2, x1, 1      # needs x1 right after it's produced
    addi x3, x2, 1
    addi x4, x3, 1
    addi x5, x4, 1
    halt
"""

# 2) Load-use hazard: lw followed immediately by a dependent add.
PROGRAMS["load_use"] = """
    addi x1, x0, 8
    sw   x1, 0(x0)
    lw   x2, 0(x0)
    add  x3, x2, x2     # depends on just-loaded x2 -> must stall
    addi x4, x3, 100
    halt
"""

# 3) MEM/WB forwarding: exactly one instruction between producer and consumer.
PROGRAMS["mem_wb_forward"] = """
    addi x1, x0, 7
    addi x9, x0, 0      # unrelated instruction in between
    add  x2, x1, x1     # x1 now 2 stages back -> forwarded from MEM/WB
    halt
"""

# 4) Branch-heavy loop: decrement x1 until zero, sum into x2. Exercises
#    control-hazard flushing repeatedly (loop taken many times).
PROGRAMS["branch_loop"] = """
    addi x1, x0, 5      # counter
    addi x2, x0, 0      # sum
LOOP:
    add  x2, x2, x1
    addi x1, x1, -1
    bne  x1, x0, LOOP
    halt
"""

# 5) Not-taken branch: confirms no flush penalty when prediction is correct.
PROGRAMS["branch_not_taken"] = """
    addi x1, x0, 1
    addi x2, x0, 2
    beq  x1, x2, SKIP   # not equal -> not taken, no flush
    addi x3, x0, 99
SKIP:
    addi x4, x0, 42
    halt
"""

# 6) jal: unconditional jump, always flushes.
PROGRAMS["jump"] = """
    jal  x1, TARGET
    addi x2, x0, 111    # should be flushed, never executes
TARGET:
    addi x3, x0, 222
    halt
"""

# 7) Edge case: two taken branches back-to-back. Confirms a flush can be
#    immediately followed by another flush with no interference between them.
PROGRAMS["back_to_back_branches"] = """
    addi x1, x0, 1
    addi x2, x0, 1
    beq  x1, x2, T1
    addi x9, x0, 999    # must be flushed
T1:
    beq  x1, x2, T2
    addi x9, x0, 888    # must also be flushed
T2:
    addi x3, x0, 42
    halt
"""

# 8) Edge case: x0 as a destination register must always read back as 0,
#    and must never "poison" forwarding for a later instruction.
PROGRAMS["x0_edge_case"] = """
    add  x0, x1, x2      # attempt to write x0 -- must stay 0, must not forward garbage
    addi x3, x0, 10       # x3 must be exactly 10, unaffected by the instruction above
    halt
"""


def run_one(name, src, forwarding=True, trace=False):
    prog = assemble(src)
    ref = RefCPU(prog).run()
    pipe = PipelineCPU(prog, forwarding=forwarding, trace=trace).run()

    ok = (pipe.regs == ref.regs) and (pipe.mem == ref.mem)
    return prog, ref, pipe, ok


def main():
    print(f"{'program':<18} {'fwd':<5} {'match':<7} {'cycles':<8} {'instrs':<8} {'CPI':<6} {'stalls':<8} {'flushes':<8}")
    all_ok = True
    for name, src in PROGRAMS.items():
        for fwd in (True, False):
            prog, ref, pipe, ok = run_one(name, src, forwarding=fwd)
            all_ok &= ok
            print(f"{name:<18} {str(fwd):<5} {str(ok):<7} {pipe.stats.cycles:<8} "
                  f"{pipe.stats.retired:<8} {pipe.stats.cpi:<6.2f} "
                  f"{pipe.stats.stalls:<8} {pipe.stats.flushes:<8}")
        print()

    print("ALL TESTS PASSED" if all_ok else "SOME TESTS FAILED")
    assert all_ok, "pipeline output diverged from reference model"


if __name__ == "__main__":
    main()
