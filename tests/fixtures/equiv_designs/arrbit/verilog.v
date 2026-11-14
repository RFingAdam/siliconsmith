// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// An instance array of a module with a localparam (`INIT`, round 16 t01): one value per element.
`default_nettype none
module arrbit_bit (
    input wire clk, input wire rst_n, input wire d, output reg q
);
  localparam INIT = 1'b1;
  always @(posedge clk) begin
    if (!rst_n) q <= INIT;
    else q <= d;
  end
endmodule

module arrbit (
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
  arrbit_bit u [7:0] (.clk(clk), .rst_n(rst_n), .d(ui_in), .q(q));
  assign uo_out = q;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, 1'b0};
endmodule
