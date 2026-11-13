
`timescale 1ns/1ps

module locs (
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

reg flag;
reg [7:0] q;
reg [7:0] r;



always @(posedge clk) begin: seq
    if (rst_n == 0) begin
        q <= 0;
    end
    else begin
        q <= (q + 1);
    end
end


always @(q, ui_in) begin: blend
    reg [8-1:0] a;
    reg [8-1:0] b;
    a = ui_in[4-1:0];
    b = 8'h0;
    if (ui_in[7]) begin
        b = q;
    end
    else if ((a == 3)) begin
        b = {a, ui_in[7-1:4]};
    end
    else if ((a > 5)) begin
        b = (~q);
    end
    else begin
        b = ui_in;
    end
    r = b;
    flag = (q[3] ^ ui_in[2]);
end



assign uo_out = (r | {flag, ui_in[7-1:1]});

endmodule
