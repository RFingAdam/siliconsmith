// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// divmod: a module instance in each branch of an if-generate whose condition is 1/0; one module holds a function that reads a port.
// The other branch is taken by Verilator, so its elaboration lacks a block that Yosys keeps: the
// check must refuse (not_proven, guard_sim_synth_differ) before the function and order checks.
`default_nettype none
module divmod_pure (input wire [7:0] a, input wire [7:0] c, output reg [7:0] o);
  function automatic [7:0] rd(input [7:0] d);
    rd = d;
  endfunction
  always @(*) o = rd(a);
  wire _unused = &{c, 1'b0};
endmodule
module divmod_mix (input wire [7:0] a, input wire [7:0] c, output reg [7:0] o);
  function automatic [7:0] rd(input [7:0] d);
    rd = d ^ c;
  endfunction
  always @(*) o = rd(a);
endmodule
`default_nettype none
module divmod (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] cnt;
  wire [7:0] y;
  always @(posedge clk)
    if (!rst_n) cnt <= 8'd0;
    else if (ui_in[0]) cnt <= cnt + 8'd1;
  localparam integer Z = 0;
  localparam integer Q = 1 / Z;
  if (Q == 0) begin : g
    divmod_pure u (.a(ui_in), .c(cnt), .o(y));
  end else begin : g
    divmod_mix u (.a(ui_in), .c(cnt), .o(y));
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, 1'b0};
endmodule
