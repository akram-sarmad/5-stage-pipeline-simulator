"""Pretty-prints a cycle-by-cycle pipeline trace table."""
from isa import assemble
from pipeline import PipelineCPU


def print_trace(name, src, forwarding=True):
    prog = assemble(src)
    cpu = PipelineCPU(prog, forwarding=forwarding, trace=True).run()

    print(f"\n=== {name} (forwarding={forwarding}) ===")
    header = f"{'cyc':<4} {'IF':<20} {'ID':<20} {'EX':<20} {'MEM':<20} {'WB':<20} {'note'}"
    print(header)
    print("-" * len(header))
    for row in cpu.trace:
        print(f"{row['cycle']:<4} {row['IF']:<20} {row['ID']:<20} {row['EX']:<20} "
              f"{row['MEM']:<20} {row['WB']:<20} {row['note']}")
    print(f"\nCycles: {cpu.stats.cycles}  Instrs retired: {cpu.stats.retired}  "
          f"CPI: {cpu.stats.cpi:.2f}  Stalls: {cpu.stats.stalls}  Flushes: {cpu.stats.flushes}")


if __name__ == "__main__":
    from tests import PROGRAMS
    # Show the two most illustrative traces: load-use stall, and a taken branch flush
    print_trace("load_use", PROGRAMS["load_use"], forwarding=True)
    print_trace("branch_loop", PROGRAMS["branch_loop"], forwarding=True)
