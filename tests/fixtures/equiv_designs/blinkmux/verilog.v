// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A blinking LED (uo_out[7] toggles every clock) and a pin that shows switch 0 while the LED is
// on and switch 1 while it is off (round 15 n40). The flip-flop has no value before reset, so the
// check from power-up must not call the multiplexer's output "x" in the netlist. Must be
// equivalent.
`default_nettype none
module blinkmux (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  reg blink;
  always @(posedge clk) begin
    if (!rst_n) blink <= 1'b0;
    else blink <= ~blink;
  end
  assign uo_out = {blink, 6'd0, blink ? ui_in[0] : ui_in[1]};
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{ena, clk, rst_n, ui_in, uio_in, 1'b0};
endmodule
