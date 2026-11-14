// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16 (round 15 review design n09): a plain PWM. duty is loaded from ui_in when uio_in[0] is
// 1; while uio_in[1] is 1 the counter runs and pwm is 1 while the counter is below duty (uo_out[0],
// its inverse on uo_out[1]). The check from reset is decided by the one-shot search (about 20 s,
// over a minute step by step): the two searches race. Must be equivalent.
`default_nettype none
module n09 (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  reg [7:0] cnt;
  reg [7:0] duty;
  reg pwm;
  always @(posedge clk) begin
    if (!rst_n) begin
      cnt <= 8'd0;
      duty <= 8'd0;
      pwm <= 1'b0;
    end else begin
      if (uio_in[0]) duty <= ui_in;
      if (uio_in[1]) begin
        cnt <= cnt + 8'd1;
        pwm <= (cnt < duty);
      end
    end
  end
  assign uo_out = {6'd0, ~pwm, pwm};
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{ena, clk, rst_n, ui_in, uio_in, 1'b0};
endmodule
