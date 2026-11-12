// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: Apache-2.0

// A counter on the FALLING clock edge. The equivalence check does not support that, so it must
// answer "not proven" (never "equivalent").

`default_nettype none

module fallingedge (
    input  wire       clk,
    input  wire       rst_n,
    output wire [7:0] uo_out
);

  reg [7:0] cnt;

  always @(negedge clk) begin
    if (!rst_n) cnt <= 8'd0;
    else cnt <= cnt + 8'd1;
  end

  assign uo_out = cnt;

endmodule
