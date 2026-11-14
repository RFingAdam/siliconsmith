// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// probe: in the generate branch Yosys keeps (1/0) and Verilator drops, an always_comb whose
// statement starts with an escaped name right after the keyword reads t and then calls a
// function that writes t (read before write). The escaped wire `\;` ends the initializer scan.
`default_nettype none
module svescw (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam integer Z = 0;
  localparam integer Q = 1 / Z;
  wire [7:0] \; ;
  assign \; = 8'd0;
  reg [7:0] y, t;
  if (Q == 0) begin : g
    always_comb begin
      t = ui_in;
      y = t ^ \; ;
    end
  end else begin : g
    function automatic [7:0] w(input [7:0] d);
      t = d;
      w = 8'd0;
    endfunction
    always_comb\y = \; ^ t ^ w(ui_in);
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, uio_in, 1'b0};
endmodule
