// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 21 (fl04): an address-width helper keeps its smallest depth in a local localparam and
// passes it on to clog2 with its argument. Verilator leaves the helper out of its elaborated
// tree (it only works it out for AW), so only the simulator lists MIN_DEPTH, with the plain
// number 2: it holds no real. Refused at bf5eb0a as a call with an argument that can hold a real.
`default_nettype none
module flocal (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic integer clog2(input integer v);
    clog2 = 0;
    for (integer i = 0; i < 31; i = i + 1)
      if ((1 << i) < v) clog2 = i + 1;
  endfunction
  function automatic integer addr_bits(input integer depth);
    localparam MIN_DEPTH = 2;
    addr_bits = clog2((depth < MIN_DEPTH) ? MIN_DEPTH : depth);
  endfunction
  localparam integer AW = addr_bits(16);
  reg [AW-1:0] addr;
  always @(posedge clk) begin
    if (!rst_n) addr <= 0;
    else addr <= addr + 1'b1;
  end
  assign uo_out = {{(8-AW){1'b0}}, addr} ^ ui_in;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
