// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// gendotx: like gendot, but the branch is chosen by 1/0 (x to Yosys, 0 to Verilator).
// The call can resolve to a different function in each tool: the check must refuse (not_proven, guard_sim_synth_differ).
`default_nettype none
module gendotx (
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
  if (Q == 0) begin : g
    function automatic [7:0] rd(input [7:0] d);
      rd = d;
    endfunction
  end else begin : g
    function automatic [7:0] rd(input [7:0] d);
      rd = d ^ cnt;
    endfunction
  end
  assign y = g.rd(ui_in);
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, 1'b0};
endmodule
