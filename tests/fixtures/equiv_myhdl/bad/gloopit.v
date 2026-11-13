

`timescale 1ns/1ps

module gloopit (
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

reg [7:0] cnt;
reg [7:0] t;



always @(posedge clk) begin: seq
    if (rst_n == 0) begin
        cnt <= 0;
    end
    else begin
        if (ui_in[0]) begin
            cnt <= (cnt + 1);
        end
    end
end


always @(cnt, t) begin: pick
    integer i;
    uo_out = 0;
    for (i=0; i<2; i=i+1) begin
        if ((i == 1)) begin
            t = cnt;
        end
        else begin
            uo_out = t;
        end
    end
end

endmodule
