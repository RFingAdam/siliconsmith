// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 25 (k07): a constant function argument narrower than the function's input. A simulator
// and Verilator work clog2(LAST + 1'b1) out at the 32-bit input's width, 16, so AW = 4 (branch
// g4, uo_out = ui_in); Yosys 0.62 works LAST + 1'b1 out at its own 4 bits first, 0, so AW = 0 in
// the gates (gx, uo_out = ~ui_in). Refused: write clog2(LAST + 1) (an unsized 1 makes the sum 32
// bits), or compute LAST + 1'b1 in a typed localparam first (k07c).
`default_nettype none
module k07 (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  /* verilator lint_off WIDTHEXPAND */
  function automatic integer clog2(input integer value);
    integer i;
    begin
      clog2 = 0;
      for (i = 0; i < 31; i = i + 1)
        if ((1 << i) < value) clog2 = i + 1;
    end
  endfunction
  localparam [3:0] LAST = 4'd15;            // last entry of a 16-entry table
  localparam AW = clog2(LAST + 1'b1);       // address bits
  /* verilator lint_on WIDTHEXPAND */
  generate if (AW == 4) begin : g4
    assign uo_out = ui_in;
  end else begin : gx
    assign uo_out = ~ui_in;
  end endgenerate
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
