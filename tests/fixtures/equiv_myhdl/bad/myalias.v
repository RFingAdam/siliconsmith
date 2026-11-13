

`timescale 1ns/1ps

module myalias (
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

wire b0;
reg [7:0] cnt;
reg [7:0] y;



always @(posedge clk) begin: seq
    if (rst_n == 0) begin
        cnt <= 0;
    end
    else begin
        cnt <= (cnt + 1);
    end
end



assign b0 = ui_in[0];


always @(b0) begin: pick
    y <= cnt;
end



assign uo_out = y;

endmodule
