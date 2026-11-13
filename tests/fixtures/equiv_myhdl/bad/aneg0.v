module aneg0 (clk, rst_n, ui_in, uo_out);
input clk;
input rst_n;
input [7:0] ui_in;
output [7:0] uo_out;
wire [7:0] uo_out;
reg [7:0] y;
wire b0;
assign b0 = ui_in[0];
reg [7:0] mem [0:2-3];
always @(posedge clk) begin: memw
    if (rst_n == 0) begin
        mem[0] <= 0;
    end
    else begin
        mem[0] <= (mem[0] + 1);
    end
end
always @(b0) begin: pick
    y = mem[0];
end
assign uo_out = y;
endmodule
