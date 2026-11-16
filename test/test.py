import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer

NOP, LOAD_W, MAC, CLEAR = 0, 1, 2, 3
LATENCY = 5

ACC_BITS = 24
ACC_MAX = (1 << (ACC_BITS - 1)) - 1
ACC_MIN = -(1 << (ACC_BITS - 1))

def to_s8(x):
    x &= 0xFF
    return x - 256 if x & 0x80 else x


def wrap24(x):
    x &= (1 << ACC_BITS) - 1
    return x - (1 << ACC_BITS) if x & (1 << (ACC_BITS - 1)) else x


class MacModel:
    """Executes ops in program order. The hardware should match this exactly,
    just LATENCY cycles later."""

    def __init__(self):
        self.weight = 0
        self.acc = 0
        self.overflow = 0
        self.valid = 0

    def step(self, op, data):
        self.valid = 0
        if op == LOAD_W:
            self.weight = to_s8(data)
        elif op == MAC:
            total = self.acc + self.weight * to_s8(data)
            if total > ACC_MAX or total < ACC_MIN:
                self.overflow = 1
            self.acc = wrap24(total)
            self.valid = 1
        elif op == CLEAR:
            self.acc = 0
            self.overflow = 0
        return (self.acc, self.overflow, self.valid)


async def reset(dut):
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)


async def issue(dut, op, data=0, byte_sel=0):
    """Drive one op for one clock cycle. Inputs change on the falling edge so
    they are stable at the rising edge."""
    dut.ui_in.value = data & 0xFF
    dut.uio_in.value = (byte_sel << 2) | op
    await FallingEdge(dut.clk)


async def drain(dut):
    """Let everything in the pipeline reach the accumulator."""
    for _ in range(LATENCY):
        await issue(dut, NOP)


async def read_acc(dut):
    """Read all three accumulator bytes through the byte-select mux and
    return the signed 24-bit value."""
    value = 0
    for sel in range(3):
        dut.uio_in.value = sel << 2
        await Timer(100, unit="ns")   # let the output mux settle
        value |= int(dut.uo_out.value) << (8 * sel)
    dut.uio_in.value = 0
    return wrap24(value)


def overflow_flag(dut):
    return (int(dut.uio_out.value) >> 7) & 1


def valid_flag(dut):
    return (int(dut.uio_out.value) >> 6) & 1



@cocotb.test()
async def test_pin_directions(dut):
    """Only uio[7:6] should be outputs."""
    await reset(dut)
    assert int(dut.uio_oe.value) == 0b1100_0000


@cocotb.test()
async def test_basic_mac(dut):
    """w = -5, then 7 and -3 -> -35 + 15 = -20."""
    await reset(dut)
    await issue(dut, LOAD_W, -5)
    await issue(dut, MAC, 7)
    await issue(dut, MAC, -3)
    await drain(dut)
    assert await read_acc(dut) == -20
    assert overflow_flag(dut) == 0


@cocotb.test()
async def test_all_products(dut):
    """Every weight against a spread of activations, including the extremes.
    Each product is checked on its own (CLEAR before each one)."""
    await reset(dut)
    activations = [-128, -127, -64, -3, -1, 0, 1, 2, 3, 85, 127]
    for w in range(-128, 128, 3):
        for a in activations:
            await issue(dut, CLEAR)
            await issue(dut, LOAD_W, w)
            await issue(dut, MAC, a)
            await drain(dut)
            got = await read_acc(dut)
            assert got == w * a, f"{w} * {a}: expected {w * a}, got {got}"


@cocotb.test()
async def test_load_then_mac_back_to_back(dut):
    """A MAC right after LOAD_W uses the new weight. A LOAD_W right after a
    MAC does not affect that MAC. No bubbles needed either way."""
    await reset(dut)
    await issue(dut, LOAD_W, 3)
    await issue(dut, MAC, 10)      # 3 * 10  = 30
    await issue(dut, LOAD_W, -7)
    await issue(dut, MAC, 10)      # -7 * 10 = -70
    await drain(dut)
    assert await read_acc(dut) == -40


@cocotb.test()
async def test_clear(dut):
    """CLEAR resets the accumulator; MACs after it start from zero."""
    await reset(dut)
    await issue(dut, LOAD_W, 100)
    for _ in range(5):
        await issue(dut, MAC, 100)
    await issue(dut, CLEAR)
    await issue(dut, MAC, 2)
    await drain(dut)
    assert await read_acc(dut) == 200


@cocotb.test()
async def test_overflow(dut):
    """-128 * -128 = 16384 each time. After 512 of them the 24-bit
    accumulator passes its max (8,388,607), so the sticky flag sets.
    CLEAR must clear it."""
    await reset(dut)
    await issue(dut, LOAD_W, -128)
    for _ in range(511):
        await issue(dut, MAC, -128)
    await drain(dut)
    assert await read_acc(dut) == 511 * 16384
    assert overflow_flag(dut) == 0

    await issue(dut, MAC, -128)
    await drain(dut)
    assert overflow_flag(dut) == 1, "overflow flag should be set"

    await issue(dut, CLEAR)
    await drain(dut)
    assert overflow_flag(dut) == 0, "CLEAR should reset the overflow flag"
    assert await read_acc(dut) == 0


@cocotb.test()
async def test_random_streaming(dut):
    """Random ops every cycle, checked against the model every cycle.
    BYTE_SEL rotates so all three bytes get checked as the stream runs."""
    await reset(dut)
    rng = random.Random(1234)
    model = MacModel()
    history = []          # model state after each issued op
    sel_prev = 0

    for i in range(1500):
        # Outputs now reflect the op issued LATENCY cycles ago
        k = i - LATENCY
        exp_acc, exp_ovf, exp_valid = history[k] if k >= 0 else (0, 0, 0)

        got_byte = int(dut.uo_out.value)
        exp_byte = ((exp_acc & 0xFFFFFF) >> (8 * sel_prev)) & 0xFF
        assert got_byte == exp_byte, f"cycle {i}: byte {sel_prev} expected {exp_byte:#04x}, got {got_byte:#04x}"
        assert overflow_flag(dut) == exp_ovf, f"cycle {i}: overflow mismatch"
        assert valid_flag(dut) == exp_valid, f"cycle {i}: valid mismatch"

        op = rng.choices([NOP, LOAD_W, MAC, CLEAR], weights=[1, 2, 8, 0.3])[0]
        data = rng.randrange(256)
        sel = i % 3
        history.append(model.step(op, data))
        await issue(dut, op, data, sel)
        sel_prev = sel