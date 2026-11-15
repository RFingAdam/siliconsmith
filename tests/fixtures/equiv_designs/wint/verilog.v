// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 23 (wint): the control of wcat and wsum, as taught code writes it. I = W - 1 and
// D = W * 2 are untyped localparams computed with an unsized number: Icarus makes them wider
// (33 and 64 bits) than Verilator and the gates (32), which the check accepts, because they
// are used as numbers only (a counter limit and a range), where every width reads the same.
// M is 8'hff + 8'h00 with a size, so it is 8 bits in every tool and {M, M} is the same
// everywhere. Equivalent.
`default_nettype none
module wint (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam W = 8;
  localparam I = W - 1;
  localparam D = W * 2;
  localparam [7:0] M = 8'hff + 8'h00;
  reg [W-1:0] count;
  wire [D-1:0] both = {M, M};

  always @(posedge clk) begin
    if (!rst_n) count <= 8'd0;
    else if (count == I) count <= 8'd0;
    else count <= count + 8'd1;
  end

  assign uo_out = count ^ both[15:8] ^ ui_in;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, both[7:0], uio_in, ena};
endmodule
