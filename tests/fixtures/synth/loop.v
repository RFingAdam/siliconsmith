`default_nettype none
module counter (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out
);
  wire [7:0] a;
  wire [7:0] b;
  assign a = b ^ ui_in;
  assign b = ~a;
  assign uo_out = a;
endmodule
