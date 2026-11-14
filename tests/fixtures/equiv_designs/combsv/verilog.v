// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// combsv: an always_comb (no always @* in the file) that reads a signal before it writes it.
// A simulator does not rerun the block for its own write: the check must refuse (not_proven, guard_sim_synth_differ).
`default_nettype none
module combsv (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] cnt;
  wire [7:0] y;
  always @(posedge clk)
    if (!rst_n) cnt <= 8'd0;
    else if (ui_in[0]) cnt <= cnt + 8'd1;
  reg [7:0] t;
  reg [7:0] yy;
  always_comb begin
    yy = t;
    t = ui_in ^ cnt;
  end
  assign y = yy;
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, 1'b0};
endmodule
