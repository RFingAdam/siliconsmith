
`timescale 1ns/1ps

module lcst (
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

wire [7:0] c;

assign c = 8'd9;


always @(ui_in, c) begin: out
    reg [8-1:0] t;
    t = 8'h0;
    t = (ui_in + c);
    if ((t > 20)) begin
        uo_out = (t - c);
    end
    else begin
        uo_out = t;
    end
end

endmodule
