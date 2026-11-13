// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// An 8-bit counter, only here to prove that the toolchain image can run cocotb on each simulator.
`default_nettype none
`timescale 1ns / 1ps

module counter (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       en,
    output reg  [7:0] count
);
  always @(posedge clk) begin
    if (!rst_n) count <= 8'd0;
    else if (en) count <= count + 8'd1;
  end
endmodule
