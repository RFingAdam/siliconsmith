
`timescale 1ns/1ps

module lists (
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

wire [1:0] sel;
wire [7:0] y;
wire [7:0] z;
reg [7:0] mem [0:4-1];



always @(posedge clk) begin: seq
    integer i;
    if (rst_n == 0) begin
        mem[0] <= 0;
        mem[1] <= 0;
        mem[2] <= 0;
        mem[3] <= 0;
    end
    else begin
        for (i=0; i<4; i=i+1) begin
            mem[i] <= ($signed({1'b0, mem[i]}) + i);
        end
    end
end



assign sel = ui_in[2-1:0];



assign y = mem[sel];



assign z = (mem[2] + mem[0]);



assign uo_out = (y ^ z);

endmodule
