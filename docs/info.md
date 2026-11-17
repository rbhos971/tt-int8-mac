<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

This project is a weight-stationary signed INT8 multiply-accumulate (MAC) unit, the basic building block of a neural network accelerator. You load an 8-bit weight once, then stream in one 8-bit activation per clock cycle. Each cycle, the design multiplies the activation by the stored weight and adds the result into a 24-bit signed accumulator.

The datapath was implemented using a 5-stage pipeline:

- Stage 1: Registers the input and op code. LOAD_W also writes the weight register here
- Stage 2: Radix-4 Booth recoding of the activation into 4 partial-product rows, followed by a carry-save adder tree that compresses them into a sum and carry vector.
- Stage 3: A 16-bit add of the sum and carry vectors, which gives the product
- Stage 4: Adds the lower 12 bits of the product into the accum, and registers the carry out
- Stage 5: Adds the high 12 bits plus the carry from stage 4, checks for overflow, and realigns the two halves for output

A few design notes:

- Booth Recoding: Radix-4 Booth turns the 8-bit activation into 4 digits from -2 to +2, so each partial product is just a mux of 0, w, or 2w, inverted for negative digits. The +1 needed for negation (~x + 1) is folded into empty low bits of the next row instead of needing its own adder. 
- The accumulator feeds back into itself, so it can't be pipelined like the rest of the datapath. It is split between two 12-bit halves with a registered carry between them. The high half runs one cycle behind the lower half, and the output register lines them back up, so the accum value on the pins is consistent.
- No stalls, one MAC per cycle. A result appears 5 cycles after an operation is issued. A MAC issued right after a LOAD_W uses the new weight, and a LOAD_W issued right after a MAC doesn't affect that MAC. 
- Overflow is handled using a sticky overflow flag. It sets the first time the 24-bit accum wraps, and stays set until a CLEAR or reset. Worst-case, the accum can sum 511 products without overflowing, with the 512th tripping the flag. 

Timing closes at ~47.6 MHz (21 ns clock period), across all 9 PVT and parasitic corners on GF180MCU, with +0.88 ns worst setup slack and 0.36 ns worst hold slack. It uses about 57% of a 1x1 tile.

### Op codes

|uio[1:0] | OP     | Effect |
|---|---|---|
|00       | NOP    | Nothing |
|01       | LOAD_W | weight <= ui_in |
|10       | MAC    | acc <= acc + weight * ui_in |
|11       | CLEAR  | acc <= 0, overflow <= 0 |

### Reading the accumulator

The 24-bit accumulator is read 8 bits at a time through `uo_out`, selected by `BYTE_SEL` on `uio[3:2]`:

| `uio[3:2]` | `uo_out` shows |
|---|---|
| `00` | `acc[7:0]` |
| `01` | `acc[15:8]` |
| `10` | `acc[23:16]` |
| `11` | `0` |

The byte select is combinational, so you can change it at any time and read the new byte right away without disturbing the pipeline.

## How to test

All values are two's complement signed 8-bit numbers.

1. Hold rst_n low for a few clock cycles, then release it.
2. Load a weight: put the weight on ui_in, set uio[1:0] = 01 for one clock cycle.
3. Stream activations: for each activation, put it on ui_in and set uio[1:0] = 10 for one clock cycle. You can issue a new MAC every cycle.
4. Set uio[1:0] = 00 (NOP) and wait at least 5 clock cycles for the pipeline to drain.
5. Read the result by setting uio[3:2] to 00, 01, and 10 in turn and reading uo_out each time. Put the three bytes together as a signed 24-bit number.
6. Check uio[7] for overflow. uio[6] pulses high for one cycle each time a MAC reaches the accumulator.
7. Set uio[1:0] = 11 for one cycle to clear the accumulator and the overflow flag before the next dot product.

Example: load weight -5, then MAC 7 and MAC -3. The result is (-5 x 7) + (-5 x -3) = -20, which reads back as 0xEC, 0xFF, 0xFF on bytes 0, 1, and 2.

The cocotb testbench in test/ checks pin directions, a spread of products including -128 and 127, back-to-back LOAD_W and MAC, CLEAR, the overflow boundary, and 1,500 random ops checked against a Python model every cycle. It passes on both the RTL and the gate-level netlist.

## External hardware

None. Everything can be driven from the Tiny Tapeout demo board's RP2040 (or any microcontroller) using the input and bidirectional pins.