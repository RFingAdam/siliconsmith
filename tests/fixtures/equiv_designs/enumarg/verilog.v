// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 26 (p01c, u13): a three-state machine with a typedef enum whose items are passed to
// helper functions: is_state(state, RUN) and code_of(RUN). An enum item holds no operator, so
// nothing in it can carry; the check refused both at 97f3bdb (a node it did not know, with a
// message that named `+`). Equivalent.
`default_nettype none
module enumarg (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  typedef enum logic [1:0] {IDLE = 2'd0, RUN = 2'd1, DONE = 2'd2} st_t;
  st_t state;
  function automatic logic is_state(input st_t s, input st_t want);
    is_state = (s == want);
  endfunction
  function automatic logic [6:0] code_of(input logic [1:0] s);
    case (s)
      2'd0: code_of = 7'h49;
      2'd1: code_of = 7'h52;
      default: code_of = 7'h44;
    endcase
  endfunction
  always @(posedge clk) begin
    if (!rst_n) state <= IDLE;
    else if (state == IDLE && ui_in[0]) state <= RUN;
    else if (state == RUN && ui_in[1]) state <= DONE;
    else if (state == DONE) state <= IDLE;
  end
  assign uo_out = {is_state(state, RUN), is_state(state, RUN) ? code_of(RUN) : code_of(IDLE)};
  assign uio_out = {1'b0, code_of(DONE)};
  assign uio_oe = 8'hFF;
  wire _unused = &{1'b0, ui_in[7:2], uio_in, ena};
endmodule
