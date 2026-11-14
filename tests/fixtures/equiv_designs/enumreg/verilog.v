// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// simulator) but the text has no `int` token. The register has no reset; it reaches a pin at cycle 42.
`default_nettype none
module enumreg (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  typedef enum {IDLE, RUN} st_t;
  st_t st;
  reg [5:0] cnt;
  always @(posedge clk) begin
    if (!rst_n) cnt <= 6'd0;
    else if (cnt != 6'd40) cnt <= cnt + 6'd1;
    if (cnt == 6'd40 && ui_in[0]) st <= (st == IDLE) ? RUN : IDLE;
  end
  assign uo_out = {7'd0, (cnt == 6'd40) ? (st == IDLE) : 1'b0};
  wire _unused = &{ui_in[7:1], uio_in, ena, 1'b0};
endmodule
