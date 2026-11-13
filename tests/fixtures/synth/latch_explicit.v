// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A latch on purpose: `always_latch` says so, so lint accepts it and synthesis maps it to a
// sky130 latch cell (and warns).

`default_nettype none

module counter (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out
);

  reg [7:0] held;

  always_latch begin
    if (ui_in[7]) held = ui_in;
  end

  reg [7:0] count;

  always @(posedge clk) begin
    if (!rst_n) count <= 8'd0;
    else count <= count + held;
  end

  assign uo_out = count;

endmodule
