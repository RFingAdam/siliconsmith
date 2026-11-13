
`timescale 1ns/1ps

module rom (
    clk,
    rst_n,
    ui_in,
    uo_out
);


input clk;
input rst_n;
input [7:0] ui_in;
output [7:0] uo_out;
wire [7:0] uo_out;

wire [7:0] c;
reg [7:0] y;

assign c = 8'd9;


always @(ui_in) begin: look
    case ((ui_in[2-1:0] % 4))
        0: y = 3;
        1: y = 7;
        2: y = 11;
        default: y = 13;
    endcase
end



assign uo_out = ((y + c) + 5);

endmodule
