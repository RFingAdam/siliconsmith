`timescale 1ns/1ps

module mylocal (
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

always @(ui_in, cnt) begin: pick
    reg [7:0] held;
    y = held;
    held = cnt;
end

assign uo_out = y;

endmodule
