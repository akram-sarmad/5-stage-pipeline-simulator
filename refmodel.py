"""Non-pipelined golden reference model: one instruction fully executes per step.
The pipelined CPU must produce identical final architectural state."""
from isa import R_OPS

MASK = 0xFFFFFFFF


def to_signed(v):
    v &= MASK
    return v - (1 << 32) if v & 0x80000000 else v


def alu(op, a, b):
    if op in ("add", "addi", "lw", "sw"):
        return (a + b) & MASK
    if op == "sub":
        return (a - b) & MASK
    if op == "and":
        return a & b
    if op == "or":
        return a | b
    raise ValueError(op)


class RefCPU:
    def __init__(self, prog, mem_words=256, max_steps=100000):
        self.prog, self.max_steps = prog, max_steps
        self.regs = [0] * 32
        self.mem = [0] * mem_words
        self.pc = 0
        self.retired = 0

    def run(self):
        steps = 0
        while 0 <= self.pc < len(self.prog) and steps < self.max_steps:
            i = self.prog[self.pc]
            steps += 1
            if i.op == "halt":
                break
            nxt = self.pc + 1
            r = self.regs
            if i.op == "nop":
                pass
            elif i.op in R_OPS:
                r[i.rd] = alu(i.op, r[i.rs1], r[i.rs2])
            elif i.op == "addi":
                r[i.rd] = alu("addi", r[i.rs1], i.imm & MASK)
            elif i.op == "lw":
                r[i.rd] = self.mem[((r[i.rs1] + i.imm) & MASK) // 4]
            elif i.op == "sw":
                self.mem[((r[i.rs1] + i.imm) & MASK) // 4] = r[i.rs2]
            elif i.op == "beq" and r[i.rs1] == r[i.rs2]:
                nxt = self.pc + i.imm
            elif i.op == "bne" and r[i.rs1] != r[i.rs2]:
                nxt = self.pc + i.imm
            elif i.op == "jal":
                r[i.rd] = self.pc + 1
                nxt = self.pc + i.imm
            r[0] = 0
            self.pc = nxt
            self.retired += 1
        return self
