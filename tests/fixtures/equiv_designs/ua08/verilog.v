// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 17 (review design ua08): the function of ua04 called with constants for a reset value,
// as in e4. Icarus resets r to 8'hf0; the gates never reset r (uo_out stays x). Refused.
`default_nettype none
module ua08 (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
/* verilator lint_off WIDTHTRUNC */
/* verilator lint_off WIDTHEXPAND */
/* verilator lint_off UNUSEDSIGNAL */
  function automatic integer pick(input [2:0] v);
    reg [1:0] m [0:1];
    begin
      m[0] = 2'd3; m[1] = 2'd0;
      if (v[m[0]]) pick = 1; else pick = 0;
    end
  endfunction
/* verilator lint_on UNUSEDSIGNAL */
/* verilator lint_on WIDTHEXPAND */
/* verilator lint_on WIDTHTRUNC */
  reg [7:0] r;
  always @(posedge clk) begin
    if (!rst_n) r <= pick(3'b111) == 0 ? 8'hf0 : 8'h0f;
    else r <= r ^ ui_in;
  end
  assign uo_out = r;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena};
endmodule
