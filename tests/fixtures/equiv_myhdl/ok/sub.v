
`timescale 1ns/1ps

module sub (
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

wire [7:0] y;
reg [7:0] cnt;




assign y = (ui_in & (~cnt));



assign uo_out = (cnt & (~ui_in));


always @(posedge clk) begin: seq
    if (rst_n == 0) begin
        cnt <= 0;
    end
    else begin
        cnt <= (cnt + 1);
    end
end

endmodule
