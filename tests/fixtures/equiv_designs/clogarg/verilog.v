// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 26 (w01, o01, q01): a pointer width that is at least one bit, maxi(1, $clog2(DEPTH)),
// the textbook way to keep $clog2(1) = 0 from making a zero-width vector. $clog2 gives a 32-bit
// integer and works its operand out at the operand's own width in every tool, so the argument
// holds nothing that Yosys can work out differently. The check refused all three at 97f3bdb (it
// did not know $clog2, and its message named `+` and a cast). AW is 4 and BW 4 in a simulator
// and in the gates: equivalent.
`default_nettype none
module clogarg (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam DEPTH = 16;
  localparam DEPTH2 = 12;
  function automatic integer maxi(input integer a, input integer b);
    maxi = (a > b) ? a : b;
  endfunction
  localparam integer AW = maxi(1, $clog2(DEPTH));
  localparam BW = maxi($clog2(DEPTH2), 1);
  reg [AW-1:0] p;
  reg [BW-1:0] q;
  always @(posedge clk) begin
    if (!rst_n) begin
      p <= {AW{1'b0}};
      q <= {BW{1'b0}};
    end else begin
      if (ui_in[0]) p <= p + 1'b1;
      if (ui_in[1]) q <= q + 1'b1;
    end
  end
  assign uo_out = {q, p};
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, ui_in[7:2], uio_in, ena};
endmodule
