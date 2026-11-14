// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// An instance array of a parameterized module (`s37_inv #(.W(2)) u [3:0]`, round 16 s37): Verilator
// keeps it as one instance with a range, a simulator makes four; each gets W = 2 in both.
`default_nettype none
module arrinv_inv #(parameter integer W = 2) (
    input wire clk, input wire rst_n, input wire [W-1:0] d, output reg [W-1:0] q
);
  localparam [W-1:0] MASK = {W{1'b1}};
  always @(posedge clk) begin
    if (!rst_n) q <= {W{1'b0}};
    else q <= d ^ MASK;
  end
endmodule

module arrinv (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  wire [7:0] q;
  arrinv_inv #(.W(2)) u [3:0] (.clk(clk), .rst_n(rst_n), .d(ui_in), .q(q));
  assign uo_out = q;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, 1'b0};
endmodule
