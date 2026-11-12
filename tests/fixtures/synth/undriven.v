`default_nettype none
module counter (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out
);
  wire [7:0] x;
  assign uo_out[3:0] = ui_in[3:0] ^ x[3:0];
  assign uo_out[7:4] = ui_in[7:4];
endmodule
