// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// The n23 UART-style transmitter in plain Verilog plus an unreset mode bit `c` that picks
// between two input pins. Stage 1 cannot pair the shift register, so the k-induction runs;
// with every flip-flop x a mux whose inputs are equal shows x in the netlist. A correct design:
// it may be not proven, never not equivalent (round 13).
`default_nettype none
module n23v (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  localparam [3:0] DIV = 4'd9;
  localparam [1:0] IDLE = 2'd0, START = 2'd1, DATA = 2'd2, STOP = 2'd3;
  reg [1:0] state;
  reg [3:0] tick;
  reg [2:0] bitn;
  reg [7:0] shreg;
  reg       txd;
  always @(posedge clk) begin
    if (!rst_n) begin
      state <= IDLE;
      tick  <= 4'd0;
      bitn  <= 3'd0;
      shreg <= 8'd0;
      txd   <= 1'b1;
    end else begin
      case (state)
        IDLE: begin
          txd <= 1'b1;
          if (ui_in[0]) begin
            shreg <= uio_in;
            state <= START;
            tick  <= 4'd0;
          end
        end
        START: begin
          txd <= 1'b0;
          if (tick == DIV) begin
            tick  <= 4'd0;
            bitn  <= 3'd0;
            state <= DATA;
          end else tick <= tick + 4'd1;
        end
        DATA: begin
          txd <= shreg[0];
          if (tick == DIV) begin
            tick  <= 4'd0;
            shreg <= {1'b0, shreg[7:1]};
            if (bitn == 3'd7) state <= STOP;
            else bitn <= bitn + 3'd1;
          end else tick <= tick + 4'd1;
        end
        default: begin
          txd <= 1'b1;
          if (tick == DIV) state <= IDLE;
          else tick <= tick + 4'd1;
        end
      endcase
    end
  end
  reg c;
  always @(posedge clk) if (ui_in[3]) c <= ui_in[4];
  assign uo_out  = {c ? ui_in[1] : ui_in[2], 4'd0, state == IDLE, state == DATA, txd};
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{ena, ui_in[7:5], 1'b0};
endmodule
