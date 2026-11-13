module myglob (clk, rst_n, ui_in, uo_out);
input clk;
input rst_n;
input [7:0] ui_in;
output [7:0] uo_out;
wire [7:0] uo_out;
reg [7:0] global;
reg [7:0] y;
wire b0;
assign b0 = ui_in[0];
always @(posedge clk) begin: seq
    if (rst_n == 0) begin
        global <= 0;
    end
    else begin
        global <= (global + 1);
    end
end
always @(b0) begin: pick
    y = global;
end
assign uo_out = y;
endmodule
