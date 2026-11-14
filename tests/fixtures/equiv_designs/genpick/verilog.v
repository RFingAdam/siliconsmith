// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// genpick: an if-generate on an ordinary constant; the branch NOT taken holds an impure function and an always @* that reads before it writes.
// Both tools take the same branch, so nothing of the other one is judged: the check must pass (equivalent).
`default_nettype none
module genpick (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] cnt;
  wire [7:0] y;
  always @(posedge clk)
    if (!rst_n) cnt <= 8'd0;
    else if (ui_in[0]) cnt <= cnt + 8'd1;
  localparam integer MODE = 2 * 3;
  if (MODE == 0) begin : g
    reg [7:0] t;
    reg [7:0] u;
    function automatic [7:0] rd(input [7:0] d);
      rd = d ^ cnt;
    endfunction
    always @(*) begin
      u = t;
      t = rd(ui_in);
    end
    assign y = u;
  end else begin : g
    reg [7:0] t;
    function automatic [7:0] inv(input [7:0] d);
      inv = ~d;
    endfunction
    always @(*) begin
      t = inv(ui_in);
    end
    assign y = t ^ cnt;
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, 1'b0};
endmodule
