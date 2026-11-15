// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 24 (fcopies, b19): a localparam inside a function, M = (P + P) / 2 - P, in two copies of
// a module. Icarus works the inner + and / out at 33 bits, so M is 0 for every P; Verilator and
// Yosys give 2147483648 for P = 32'h8000_0001. Copy ua calls the function at run time, so the
// tree lists M there (0, as Icarus); copy ub only uses it in a range, and the tree leaves it out.
// The values both tools listed agreed: Icarus gives uo_out = e9 for ui_in = 0f, the gates 19, and
// the check said equivalent at d820087. Refused: the tree does not list ub's copy.
`default_nettype none
module fcopies_sub #(parameter [31:0] P = 0) (input wire [7:0] i, output wire [7:0] o);
  /* verilator lint_off WIDTHTRUNC */
  /* verilator lint_off WIDTHEXPAND */
  function automatic integer f(input integer a);
    localparam M = (P + P) / 32'd2 - P;
    f = a + ((M == 0) ? 7 : 3);
  endfunction
  if (P == 32'd1) begin : ga
    assign o = i ^ f(i);
  end else begin : gb
    wire [f(0):0] w = {8{i[0]}} ^ i;
    assign o = w;
  end
  /* verilator lint_on WIDTHEXPAND */
  /* verilator lint_on WIDTHTRUNC */
endmodule

module fcopies (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  wire [7:0] oa, ob;
  fcopies_sub #(.P(32'd1)) ua (.i(ui_in), .o(oa));
  fcopies_sub #(.P(32'h8000_0001)) ub (.i(ui_in), .o(ob));
  assign uo_out = oa ^ ob;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
