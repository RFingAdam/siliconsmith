// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// divcomb: an always @* in each branch of an if-generate on 1/0; the branch Yosys keeps reads a signal before it writes it.
// The tree tool reads the constant as 0 and drops the other branch, so the order and purity
// checks must refuse when its tree holds fewer blocks than the text declares.
`default_nettype none
module divcomb (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] cnt;
  wire [7:0] y;
  always @(posedge clk)
    if (!rst_n) cnt <= 8'd0;
    else if (ui_in[0]) cnt <= cnt + 8'd1;
  localparam integer Z = 0;
  localparam integer Q = 1 / Z;
  reg [7:0] yy;
  reg [7:0] t;
  if (Q == 0) begin : g
    always @(*) begin
      t = ui_in;
      yy = t;
    end
  end else begin : g
    always @(*) begin
      yy = t;
      t = ui_in ^ cnt;
    end
  end
  assign y = yy;
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, 1'b0};
endmodule
