
`timescale 1ns/1ps

module lfor (
    clk,
    rst_n,
    ui_in,
    uo_out
);


input clk;
input rst_n;
input [7:0] ui_in;
output [7:0] uo_out;
reg [7:0] uo_out;




always @(ui_in) begin: cnt
    integer n;
    integer i;
    n = 0;
    for (i=0; i<8; i=i+1) begin
        if (ui_in[i]) begin
            n = n + 1;
        end
    end
    uo_out = n;
end

endmodule
