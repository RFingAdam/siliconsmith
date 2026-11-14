// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Combinational blocks that build signals piece by piece before reading them: constant selects,
// a full case and casez without default, a loop accumulator, a named-block local, array elements,
// struct members. Every read comes after the write on every path: equivalent.
`default_nettype none
module pieces (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  typedef struct packed { logic [3:0] a; logic [3:0] b; } pair_t;
  reg [7:0] y1, t1, y2, t2, y3, y4, acc, y5, y6, y9, y10;
  reg [8:1] x;
  pair_t s;
  reg [7:0] m [0:1];
  integer i;
  // piecewise constant selects, an if between them so they stay two writes
  always @(*) begin
    t1[3:0] = ui_in[3:0];
    if (ui_in[7]) y1 = 8'd1; else y1 = 8'd2;
    t1[7:4] = uio_in[7:4];
    y1 = y1 ^ t1;
  end
  // a full case without default, then a read
  always @(*) begin
    case (ui_in[1:0])
      2'd0: t2 = 8'd3;
      2'd1: t2 = uio_in;
      2'd2: t2 = ~uio_in;
      2'd3: t2 = 8'd9;
    endcase
    y2 = t2;
  end
  // a full casez without default
  always @(*) begin
    casez (ui_in[3:2])
      2'b1?: y3 = 8'd1;
      2'b01: y3 = 8'd2;
      2'b00: y3 = 8'd3;
    endcase
    y3 = y3 + 8'd1;
  end
  // a loop variable read after its init, an accumulator written before the loop
  always @(*) begin
    acc = 8'd0;
    for (i = 0; i < 8; i = i + 1)
      if (ui_in[i]) acc = acc + 8'd1;
    y4 = acc;
  end
  // a named-block local written first
  always @(*) begin : nb
    reg [7:0] loc;
    loc = uio_in;
    y5 = loc ^ 8'd1;
  end
  // constant array elements
  always @(*) begin
    m[0] = ui_in;
    m[1] = uio_in;
    y6 = m[0] ^ m[1];
  end
  // a descending range from 1, built in pieces, struct members in pieces
  always @(*) begin
    x[4:1] = ui_in[3:0];
    if (ui_in[6]) y9 = 8'd0; else y9 = 8'd1;
    x[8:5] = uio_in[3:0];
    s.a = ui_in[7:4];
    if (ui_in[5]) y9 = y9 + 8'd2;
    s.b = uio_in[7:4];
    y9 = y9 ^ x ^ s;
  end
  // if / else if chain, all branches write
  always @(*) begin
    if (ui_in[0]) y10 = 8'd1;
    else if (ui_in[1]) y10 = 8'd2;
    else y10 = 8'd3;
    y10 = y10 + uio_in;
  end
  assign uo_out = y1 ^ y2 ^ y3 ^ y4 ^ y5 ^ y6 ^ y9 ^ y10;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, 1'b0};
endmodule
