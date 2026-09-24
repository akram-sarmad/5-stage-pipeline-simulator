"""Tiny RV32I-subset assembler and instruction representation.

Supported: add sub and or addi lw sw beq bne jal nop halt
Labels are supported for branch/jump targets. Immediates for branches/jal are
resolved to *instruction-index offsets* (not bytes) to keep the simulator simple.
"""
import re
from dataclasses import dataclass
from typing import Optional

REGS = {f"x{i}": i for i in range(32)}
REGS.update({"zero": 0, "ra": 1, "sp": 2})

R_OPS = {"add", "sub", "and", "or"}
I_OPS = {"addi"}
LOAD_OPS = {"lw"}
STORE_OPS = {"sw"}
BRANCH_OPS = {"beq", "bne"}
JUMP_OPS = {"jal"}


@dataclass
class Instr:
    op: str
    rd: Optional[int] = None
    rs1: Optional[int] = None
    rs2: Optional[int] = None
    imm: int = 0
    text: str = ""

    def writes_reg(self):
        return self.op in R_OPS | I_OPS | LOAD_OPS | JUMP_OPS and self.rd not in (None, 0)

    def reads(self):
        """Source registers actually read by this instruction."""
        if self.op in R_OPS or self.op in BRANCH_OPS or self.op in STORE_OPS:
            return [r for r in (self.rs1, self.rs2) if r is not None]
        if self.op in I_OPS or self.op in LOAD_OPS:
            return [self.rs1]
        return []


NOP = Instr("nop", text="nop")
HALT = Instr("halt", text="halt")


def _reg(tok):
    tok = tok.strip()
    if tok not in REGS:
        raise ValueError(f"unknown register '{tok}'")
    return REGS[tok]


def assemble(src: str):
    """Two-pass assembler. Returns a list of Instr."""
    lines = []
    for raw in src.splitlines():
        line = raw.split("#")[0].strip()
        if line:
            lines.append(line)

    # Pass 1: collect labels
    labels, body = {}, []
    for line in lines:
        while ":" in line:
            label, line = line.split(":", 1)
            labels[label.strip()] = len(body)
            line = line.strip()
        if line:
            body.append(line)

    # Pass 2: encode
    prog = []
    for idx, line in enumerate(body):
        parts = re.split(r"[,\s]+", line.replace("(", " ").replace(")", " ").strip())
        op, args = parts[0].lower(), [a for a in parts[1:] if a]
        if op == "nop":
            prog.append(Instr("nop", text=line)); continue
        if op == "halt":
            prog.append(Instr("halt", text=line)); continue
        if op in R_OPS:
            rd, rs1, rs2 = (_reg(a) for a in args)
            prog.append(Instr(op, rd, rs1, rs2, text=line))
        elif op in I_OPS:
            rd, rs1 = _reg(args[0]), _reg(args[1])
            prog.append(Instr(op, rd, rs1, imm=int(args[2], 0), text=line))
        elif op in LOAD_OPS:
            rd, off, rs1 = _reg(args[0]), int(args[1], 0), _reg(args[2])
            prog.append(Instr(op, rd, rs1, imm=off, text=line))
        elif op in STORE_OPS:
            rs2, off, rs1 = _reg(args[0]), int(args[1], 0), _reg(args[2])
            prog.append(Instr(op, rs1=rs1, rs2=rs2, imm=off, text=line))
        elif op in BRANCH_OPS:
            rs1, rs2, target = _reg(args[0]), _reg(args[1]), args[2]
            off = labels[target] - idx if target in labels else int(target, 0)
            prog.append(Instr(op, rs1=rs1, rs2=rs2, imm=off, text=line))
        elif op in JUMP_OPS:
            rd, target = _reg(args[0]), args[1]
            off = labels[target] - idx if target in labels else int(target, 0)
            prog.append(Instr(op, rd, imm=off, text=line))
        else:
            raise ValueError(f"unsupported instruction: {line}")
    return prog
