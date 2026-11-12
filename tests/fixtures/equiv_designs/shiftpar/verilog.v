// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: Apache-2.0

// 16-bit shift register fed by ui_in[0]. Only the parity (the XOR of all 16 bits) leaves the
// chip, on uo_out[0]; the register contents are hidden state.

`default_nettype none

module shiftpar (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out
);

  reg [15:0] sr;

  always @(posedge clk) begin
    if (!rst_n) sr <= 16'd0;
    else sr <= {sr[14:0], ui_in[0]};
  end

  assign uo_out = {7'd0, ^sr};

  // ui_in[7:1] are not used.
  wire _unused = &{ui_in[7:1], 1'b0};

endmodule
