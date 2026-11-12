// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: Apache-2.0

// SEEDED BUG: this counter has no reset. It relies on `initial count = 0;`, which a simulator
// honours but real gates ignore: a flip-flop powers up as an unknown 0 or 1. RTL simulation
// passes; gate-level simulation shows the output as unknown (x). Used to test the gate-level
// first-difference report.

`default_nettype none

module counter (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out
);

  reg [7:0] count;

  initial count = 8'd0;

  always @(posedge clk) begin
    if (ui_in[0]) count <= count + 8'd1;
  end

  assign uo_out = count;

  // rst_n and ui_in[7:1] are not used: that is the bug.
  wire _unused = &{rst_n, ui_in[7:1], 1'b0};

endmodule
