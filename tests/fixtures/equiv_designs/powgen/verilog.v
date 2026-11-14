// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// powgen: like divgen, with the constant 0 ** -1.
// The other branch is taken by Verilator, so its elaboration lacks a block that Yosys keeps: the
// check must refuse (not_proven, guard_sim_synth_differ) before the function and order checks.
`default_nettype none
module powgen (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] cnt;
  wire [7:0] y;
  always @(posedge clk)
    if (!rst_n) cnt <= 8'd0;
    else if (ui_in[0]) cnt <= cnt + 8'd1;
  localparam integer Q = 0 ** -1;
  if (Q == 0) begin : g
    function automatic [7:0] rd(input [7:0] d);
      rd = d;
    endfunction
    assign y = rd(ui_in);
  end else begin : g
    function automatic [7:0] rd(input [7:0] d);
      rd = d ^ cnt;
    endfunction
    reg [7:0] yy;
    always @(*) yy = rd(ui_in);
    assign y = yy;
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, 1'b0};
endmodule
