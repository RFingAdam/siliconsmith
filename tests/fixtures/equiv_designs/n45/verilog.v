// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A function that reads the members of a packed struct argument (`p.hi`): normal SystemVerilog, equivalent.
`default_nettype none
module n45 (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  // packed struct passed to a function that reads its fields
  typedef struct packed {
    logic [3:0] hi;
    logic [3:0] lo;
  } pair_t;
  function automatic [3:0] larger(input pair_t p);
    larger = (p.hi > p.lo) ? p.hi : p.lo;
  endfunction
  pair_t sample;
  always_ff @(posedge clk) begin
    if (!rst_n) sample <= '0;
    else sample <= ui_in;
  end
  assign uo_out  = {4'd0, larger(sample)};
  assign uio_out = '0;
  assign uio_oe  = '0;
  wire _unused = &{ena, uio_in, 1'b0};
endmodule
