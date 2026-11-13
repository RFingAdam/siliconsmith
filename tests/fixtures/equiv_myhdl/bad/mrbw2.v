

`timescale 1ns/1ps

module mrbw2 (
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


always @(ui_in, cnt, t) begin: pick
    if (ui_in[1]) begin
        uo_out = t;
    end
    else begin
        uo_out = 0;
    end
    t = cnt;
end

endmodule
