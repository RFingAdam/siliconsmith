// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 19 control: a child module that is only instantiated in a generate branch the
// parameters do not choose, with its own parameter passed to a helper with a proven select. No tool
// builds the child, so its parameter needs no value. Equivalent.
`default_nettype none
module deadkid_filt #(parameter [2:0] K = 3'd3) (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] a,
    output reg  [7:0] y
);
  function automatic getbit;
    input [7:0] v;
    input [2:0] k;
    getbit = v[k];
  endfunction
  always @(posedge clk) begin
    if (!rst_n) y <= 8'd0;
    else y <= a ^ {7'd0, getbit(8'b0110_1100, K)};
  end
endmodule
module deadkid (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  parameter USE_FILTER = 0;
  generate
    if (USE_FILTER) begin : g_f
      deadkid_filt #(.K(3'd5)) u (.clk(clk), .rst_n(rst_n), .a(ui_in), .y(uo_out));
    end else begin : g_n
      assign uo_out = ui_in;
    end
  endgenerate
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, uio_in, 1'b0};
endmodule
