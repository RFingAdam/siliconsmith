// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// probe: a function at module level reads `g.k`. In the generate branch Verilator keeps (1/0 is 0
// to it) `g.k` is a localparam, which Verilator folds to a constant, so the function looks pure;
// in the branch Yosys and a simulator keep (1/0 is x) `g.k` is a wire driven by the counter.
// The call itself is at the same place for both tools, and g holds no process.
`default_nettype none
module hierk (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] cnt;
  always @(posedge clk)
    if (!rst_n) cnt <= 8'd0;
    else if (ui_in[0]) cnt <= cnt + 8'd1;
  localparam integer Z = 0;
  localparam integer Q = 1 / Z;
  if (Q == 0) begin : g
    localparam [7:0] k = 8'h0f;
  end else begin : g
    wire [7:0] k;
    assign k = cnt;
  end
  function automatic [7:0] rd(input [7:0] d);
    rd = d ^ g.k;
  endfunction
  reg [7:0] y;
  always @(*) y = rd(ui_in);
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, 1'b0};
endmodule
