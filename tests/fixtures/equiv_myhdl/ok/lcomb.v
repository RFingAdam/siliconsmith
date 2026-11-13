
`timescale 1ns/1ps

module lcomb (
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

reg [1:0] sel;
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


always @(sel, mem[0], mem[1], mem[2], mem[3], ui_in) begin: rd
    reg [8-1:0] v;
    sel = ui_in[2-1:0];
    v = 8'h0;
    v = mem[sel];
    if ((v > 3)) begin
        uo_out = (v + mem[1]);
    end
    else begin
        uo_out = mem[3];
    end
end

endmodule
