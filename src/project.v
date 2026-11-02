/*
 * Copyright (c) 2024 Your Name
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module tt_um_example (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  wire [1:0]  op       = uio_in[1:0];
  wire [1:0]  byte_sel = uio_in[3:2];
 
  wire signed [23:0] acc;
  wire valid;
  wire overflow;
 
  mac u_mac (
      .clk      (clk),
      .rst_n    (rst_n),
      .data_in  (ui_in),
      .op       (op),
      .acc      (acc),
      .valid    (valid),
      .overflow (overflow)
  );

  assign uo_out = (byte_sel == 2'd0) ? acc[7:0]   :
                  (byte_sel == 2'd1) ? acc[15:8]  :
                  (byte_sel == 2'd2) ? acc[23:16] :
                                       8'd0;

  assign uio_oe  = 8'b1100_0000;
  assign uio_out = {overflow, valid, 6'b0};


  // List all unused inputs to prevent warnings
  wire _unused = &{ena, uio_in[7:4], 1'b0};

endmodule
