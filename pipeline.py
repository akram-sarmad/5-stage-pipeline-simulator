"""Cycle-accurate 5-stage (IF, ID, EX, MEM, WB) RISC-V-subset pipeline simulator.

Hazard handling (each individually switchable for experiments):
  * Data hazards  -> forwarding from EX/MEM and MEM/WB into EX (forwarding=True),
                     otherwise stall in ID until the producer has written back.
  * Load-use      -> 1-cycle stall (bubble inserted into EX) even with forwarding.
  * Control       -> branches/jal resolved in EX; taken => flush IF and ID (2 bubbles),
                     with a static predict-not-taken policy.

Register file is write-first: a WB write in a cycle is visible to the ID read in the same cycle.
"""
from dataclasses import dataclass, field
from typing import Optional
from isa import Instr, NOP, R_OPS
from refmodel import alu, MASK


@dataclass
class IFID:
    instr: Instr = field(default_factory=lambda: NOP)
    pc: int = -1
    valid: bool = False


@dataclass
class IDEX:
    instr: Instr = field(default_factory=lambda: NOP)
    pc: int = -1
    a: int = 0          # rs1 value read in ID
    b: int = 0          # rs2 value read in ID
    valid: bool = False


@dataclass
class EXMEM:
    instr: Instr = field(default_factory=lambda: NOP)
    pc: int = -1
    alu_out: int = 0
    store_val: int = 0
    valid: bool = False


@dataclass
class MEMWB:
    instr: Instr = field(default_factory=lambda: NOP)
    pc: int = -1
    result: int = 0
    valid: bool = False


@dataclass
class Stats:
    cycles: int = 0
    retired: int = 0
    stalls: int = 0
    flushes: int = 0       # number of flush *events* (taken control-flow)
    bubbles_flush: int = 0  # bubbles created by flushes
    forwards: int = 0

    @property
    def cpi(self):
        return self.cycles / self.retired if self.retired else float("nan")


class PipelineCPU:
    def __init__(self, prog, forwarding=True, mem_words=256, max_cycles=200000, trace=False):
        self.prog = prog
        self.forwarding = forwarding
        self.max_cycles = max_cycles
        self.trace_enabled = trace
        self.trace = []
        self.regs = [0] * 32
        self.mem = [0] * mem_words
        self.pc = 0
        self.ifid, self.idex, self.exmem, self.memwb = IFID(), IDEX(), EXMEM(), MEMWB()
        self.stats = Stats()
        self.halted = False       # halt has reached WB
        self.fetch_stopped = False  # halt fetched; stop fetching further

    # ---------------------------------------------------------------- helpers
    def _fetch(self, pc):
        return self.prog[pc] if 0 <= pc < len(self.prog) else None

    def _load_use_hazard(self):
        """Instruction in ID needs a register that the load currently in EX will produce."""
        ex = self.idex
        if not (ex.valid and ex.instr.op == "lw" and ex.instr.rd):
            return False
        return self.ifid.valid and ex.instr.rd in self.ifid.instr.reads()

    def _raw_stall_no_fwd(self):
        """Without forwarding: stall while any older in-flight instr will write a source reg."""
        srcs = self.ifid.instr.reads() if self.ifid.valid else []
        for stage in (self.idex, self.exmem, self.memwb):
            if stage.valid and stage.instr.writes_reg() and stage.instr.rd in srcs and stage.instr.rd != 0:
                # memwb result is written this cycle and readable in ID (write-first RF) -> no stall
                if stage is self.memwb:
                    continue
                return True
        return False

    def _fwd(self, reg, fallback):
        """Forward newest value for `reg` into EX; returns (value, forwarded?).

        Two forwarding sources, checked newest-first:
          - EXMEM: the producer is exactly 1 instruction ahead of the
            consumer (producer is in MEM while consumer is in EX). Its ALU
            result already exists here. Excluded for `lw`: a load's real
            value isn't ready until MEM finishes, so at this point EXMEM
            only holds the *address*, not the loaded data -- forwarding it
            here would forward garbage. (This case is instead handled by
            _load_use_hazard's stall, one cycle earlier.)
          - MEMWB: the producer is exactly 2 instructions ahead (one
            unrelated instruction sits between producer and consumer), so by
            the time the consumer reaches EX, the producer's result has
            already moved past EXMEM into MEMWB, one step from the register
            file but not there yet. Without this path the consumer would
            read the stale pre-write value from self.regs.
        EXMEM is checked first because it's the freshest value for that
        register, should both a same-register EXMEM and MEMWB entry exist.
        """
        if reg == 0:
            return 0, False
        if self.forwarding:
            m = self.exmem
            if m.valid and m.instr.writes_reg() and m.instr.rd == reg and m.instr.op != "lw":
                return m.alu_out, True
            w = self.memwb
            if w.valid and w.instr.writes_reg() and w.instr.rd == reg:
                return w.result, True
        return fallback, False

    # ------------------------------------------------------------------ cycle
    def step(self):
        """Advance the pipeline by one cycle.

        KEY IDEA: in real hardware all 5 stages update simultaneously on one
        clock edge. Software can't do that -- statements run one at a time --
        so every stage below computes its "new_*" pipeline register using
        ONLY old, start-of-cycle state (self.ifid, self.idex, self.exmem,
        self.memwb, self.regs before WB's write). None of the new_* values
        are written back into self.* until the single assignment at the very
        end of this method, so no stage can accidentally see another stage's
        already-updated output from this same cycle.

        The ONE deliberate exception is the register file: WB's write happens
        first (below) so that ID's read of self.regs later in this same
        method sees the fresh value (write-first regfile). That single
        same-cycle dependency is the entire reason WB is computed before ID
        in this function -- every other stage here is independent of order.
        """
        st = self.stats
        st.cycles += 1

        # ---------- WB (uses state from start of cycle) ----------
        wb = self.memwb
        if wb.valid:
            if wb.instr.op == "halt":
                self.halted = True
            elif wb.instr.op != "nop":
                st.retired += 1
                if wb.instr.writes_reg():
                    self.regs[wb.instr.rd] = wb.result & MASK
                self.regs[0] = 0

        # ---------- MEM ----------
        ex = self.exmem
        new_memwb = MEMWB(instr=ex.instr, pc=ex.pc, valid=ex.valid)
        if ex.valid:
            if ex.instr.op == "lw":
                new_memwb.result = self.mem[(ex.alu_out & MASK) // 4]
            elif ex.instr.op == "sw":
                self.mem[(ex.alu_out & MASK) // 4] = ex.store_val & MASK
            else:
                new_memwb.result = ex.alu_out

        # ---------- EX ----------
        de = self.idex
        new_exmem = EXMEM(instr=de.instr, pc=de.pc, valid=de.valid)
        redirect: Optional[int] = None
        if de.valid and de.instr.op not in ("nop", "halt"):
            i = de.instr
            a, fa = self._fwd(i.rs1, de.a) if i.rs1 is not None else (0, False)
            b, fb = self._fwd(i.rs2, de.b) if i.rs2 is not None else (0, False)
            st.forwards += int(fa) + int(fb)
            if i.op in R_OPS:
                new_exmem.alu_out = alu(i.op, a, b)
            elif i.op in ("addi", "lw"):
                new_exmem.alu_out = alu("addi", a, i.imm & MASK)
            elif i.op == "sw":
                new_exmem.alu_out = alu("addi", a, i.imm & MASK)
                new_exmem.store_val = b
            elif i.op == "beq" and a == b:
                redirect = de.pc + i.imm
            elif i.op == "bne" and a != b:
                redirect = de.pc + i.imm
            elif i.op == "jal":
                new_exmem.alu_out = de.pc + 1
                redirect = de.pc + i.imm

        # ---------- ID (hazard detection) ----------
        stall = False
        if redirect is None:
            stall = self._load_use_hazard() if self.forwarding else self._raw_stall_no_fwd()

        if redirect is not None:
            # Branch/jal resolved HERE, in EX (stage 3 of 5). By the time an
            # instruction reaches EX, exactly 2 younger instructions have
            # already been fetched behind it (one now in ID, one now in IF)
            # under the assumption "not taken". If taken, both are wrong and
            # must be discarded -- flush IF/ID (turn them into bubbles) and
            # redirect the PC to the real target. Resolving in an earlier
            # stage would flush fewer instructions but cost extra hardware.
            st.flushes += 1
            st.bubbles_flush += 2
            new_idex = IDEX()
            new_ifid = IFID()
            self.pc = redirect
            self.fetch_stopped = False
        elif stall:
            st.stalls += 1
            new_idex = IDEX()       # bubble into EX
            new_ifid = self.ifid    # hold IF/ID
        else:
            f = self.ifid
            new_idex = IDEX(instr=f.instr, pc=f.pc, valid=f.valid)
            if f.valid:
                i = f.instr
                # write-first regfile: WB write from this cycle already applied above
                if i.rs1 is not None:
                    new_idex.a = self.regs[i.rs1]
                if i.rs2 is not None:
                    new_idex.b = self.regs[i.rs2]

            # ---------- IF ----------
            new_ifid = IFID()
            if not self.fetch_stopped:
                instr = self._fetch(self.pc)
                if instr is not None:
                    new_ifid = IFID(instr=instr, pc=self.pc, valid=True)
                    if instr.op == "halt":
                        self.fetch_stopped = True
                    else:
                        self.pc += 1

        if self.trace_enabled:
            self.trace.append(self._snapshot(wb, ex, de, stall, redirect))

        self.memwb, self.exmem, self.idex, self.ifid = new_memwb, new_exmem, new_idex, new_ifid

    # ---------------------------------------------------------------- driver
    def run(self):
        """Advance until halt retires, or the pipeline drains after running
        off the end of the program (self.ran_off_end is set in that case --
        this happens if a branch/jal target lands outside the program, or a
        program is missing a final `halt`). Draining without halting is not
        a crash, but it IS a sign of a likely program bug -- check
        ran_off_end rather than assuming a clean exit means correct behavior.
        """
        self.ran_off_end = False
        while not self.halted and self.stats.cycles < self.max_cycles:
            self.step()
            # ran off the end without halt: drain
            if (self.fetch_stopped is False and self._fetch(self.pc) is None
                    and not any(s.valid for s in (self.ifid, self.idex, self.exmem, self.memwb))):
                self.ran_off_end = True
                break
        return self

    def _snapshot(self, wb, ex, de, stall, redirect):
        def name(valid, instr):
            return instr.text if valid and instr.op != "nop" else "--"
        return {
            "cycle": self.stats.cycles,
            "IF": self._fetch(self.pc).text if (not self.fetch_stopped and self._fetch(self.pc)) else "--",
            "ID": name(self.ifid.valid, self.ifid.instr),
            "EX": name(de.valid, de.instr),
            "MEM": name(ex.valid, ex.instr),
            "WB": name(wb.valid, wb.instr),
            "note": "STALL(load-use/RAW)" if stall else (f"FLUSH -> pc={redirect}" if redirect is not None else ""),
        }
