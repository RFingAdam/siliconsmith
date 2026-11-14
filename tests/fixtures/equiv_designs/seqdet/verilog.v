// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Sequence detector (a small state machine): uo_out[0] is 1 for one cycle after the serial input
// ui_in[0] has shown 1, 0, 1, 1. Overlapping matches count. Five states in a case statement.

`default_nettype none

module seqdet (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out
);

  localparam [2:0] IDLE = 3'd0, S1 = 3'd1, S10 = 3'd2, S101 = 3'd3, S1011 = 3'd4;

  reg [2:0] state;
  reg [2:0] next;
  wire x = ui_in[0];

  always @(*) begin
    case (state)
      IDLE:    next = x ? S1 : IDLE;
      S1:      next = x ? S1 : S10;
      S10:     next = x ? S101 : IDLE;
      S101:    next = x ? S1011 : S10;
      S1011:   next = x ? S1 : S10;
      default: next = IDLE;
    endcase
  end

  always @(posedge clk) begin
    if (!rst_n) state <= IDLE;
    else state <= next;
  end

  assign uo_out = {7'd0, state == S1011};

  // ui_in[7:1] are not used.
  wire _unused = &{ui_in[7:1], 1'b0};

endmodule
