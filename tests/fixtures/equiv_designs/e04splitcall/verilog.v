// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A call split over two lines (`rd\n(ui_in)`) in a generate branch chosen by `1 / 0`.
`default_nettype none
module e04splitcall (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] cnt;
  always @(posedge clk)
    if (!rst_n) cnt <= 8'd0;
    else if (ui_in[0]) cnt <= cnt + 8'd1;
  localparam integer Z = 0;
  localparam integer Q = 1 / Z;
  wire [7:0] y;
  if (Q == 0) begin : g
    assign y = ui_in;
  end else begin : g
    function automatic [7:0] rd(input [7:0] d);
      rd = d ^ cnt;
    endfunction
    assign y = rd
      (ui_in);
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, 1'b0};
endmodule
