// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A function with an output argument, called on one branch of an `if`; `t` is read after it.
// In Verilator's tree the if/else is a ternary; a simulator keeps the old `t` on the else path,
// the gates do not. Icarus cannot compile the function at all (round 13).
`default_nettype none
module fnoutif (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic [7:0] f(input [7:0] a, output [7:0] o);
    begin
      o = a;
      f = ~a;
    end
  endfunction
  reg [7:0] y, t, z;
  always @(*) begin
    if (ui_in[0]) y = f(uio_in, t);
    else y = 8'd0;
    z = t;
  end
  assign uo_out = z;
  assign uio_out = y;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, ui_in[7:1], 1'b0};
endmodule
