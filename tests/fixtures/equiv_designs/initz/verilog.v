// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: Apache-2.0

// A free-running counter that relies on an INITIAL value (an `initial` block) and has no reset. A
// simulator starts it at 0; real silicon does not. The equivalence check must refuse this.

`default_nettype none

module initz (
    input  wire       clk,
    input  wire       rst_n,
    output wire [7:0] uo_out
);

  reg [7:0] cnt;

  initial cnt = 8'd0;

  always @(posedge clk) cnt <= cnt + 8'd1;

  assign uo_out = cnt;

  wire _unused = &{rst_n, 1'b0};

endmodule
