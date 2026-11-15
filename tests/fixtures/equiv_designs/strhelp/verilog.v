// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 20: text as 8-bit values, passed to helper functions in plain assigns: a sized string
// localparam (`localparam [39:0] MSG = "HELLO"`) and a child's sized string parameter that an
// override sets to another letter. A simulator lists a string parameter as a string, but its
// packed range makes it hold bits, so no real can reach the calls through it: equivalent.
`default_nettype none
module strhelp_echo #(parameter [7:0] CH = "A") (
    input  wire [7:0] a,
    output wire [7:0] y
);
  function automatic [7:0] to_lower(input [7:0] c);
    to_lower = (c >= 8'h41 && c <= 8'h5A) ? c + 8'd32 : c;
  endfunction
  assign y = (a == to_lower(CH)) ? 8'hFF : 8'h00;
endmodule

module strhelp (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam [39:0] MSG = "HELLO";
  function automatic [7:0] mix(input [39:0] s);
    mix = s[39:32] ^ s[31:24] ^ s[23:16] ^ s[15:8] ^ s[7:0];
  endfunction
  wire [7:0] hit;
  strhelp_echo #(.CH("Q")) e0 (.a(ui_in), .y(hit));
  assign uo_out = (mix(MSG) ^ ui_in) & ~hit;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
