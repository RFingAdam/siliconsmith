// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A function that reads the members of a packed struct argument `cfg`, and a sub-module port
// connected by name with the same name (`.cfg(cfg)`, round 14 v73). A named port connection is
// not a scope, so `cfg.hi` is a member of the argument, not a hierarchical name. Must be
// equivalent.
`default_nettype none
module structport (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  // a packed struct passed to a function; a sub-module port has the same name as the argument
  typedef struct packed {
    logic [3:0] hi;
    logic [3:0] lo;
  } pair_t;
  function automatic [3:0] pick(input pair_t cfg, input sel);
    pick = sel ? cfg.hi : cfg.lo;
  endfunction
  pair_t cfg;
  assign cfg = uio_in;
  wire [3:0] r;
  structport_reg u_reg (.clk(clk), .rst_n(rst_n), .cfg(cfg), .q(r));
  assign uo_out = {pick(cfg, ui_in[0]), r};
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{ena, ui_in, uio_in, clk, rst_n, 1'b0};
endmodule

module structport_reg (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] cfg,
    output reg  [3:0] q
);
  always @(posedge clk) begin
    if (!rst_n) q <= 4'd0;
    else q <= cfg[3:0] ^ cfg[7:4];
  end
endmodule
