`default_nettype none
module counter (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out
);
  assign uo_out = ui_in;
  assign uo_out = ~ui_in;
endmodule
