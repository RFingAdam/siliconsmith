// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 25: the control of k07 and kd4. The same values written so that every tool works
// them out at the input's width: an unsized 1 (LAST + 1 is 32 bits), a typed localparam computed
// first (N), and an operand widened to the 8-bit input ({4'd0, LAST} + 8'd1). AW is 4 and D 16 in
// a simulator and in the gates, so uo_out = ui_in ^ 04 in both. Equivalent.
`default_nettype none
module k07c (
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
  function automatic [7:0] f(input [7:0] a);
    f = a;
  endfunction
  localparam [3:0] LAST = 4'd15;
  localparam integer N = LAST + 1'b1;
  localparam integer AW = clog2(LAST + 1);
  localparam integer AN = clog2(N);
  localparam integer D = f({4'd0, LAST} + 8'd1);
  localparam integer Q = 64 / D;
  /* verilator lint_on WIDTHEXPAND */
  generate if (AW == 4 && AN == 4) begin : g4
    assign uo_out = ui_in ^ Q[7:0];
  end else begin : gx
    assign uo_out = ~ui_in;
  end endgenerate
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
