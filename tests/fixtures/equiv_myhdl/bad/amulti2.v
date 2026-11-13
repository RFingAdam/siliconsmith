module amulti2 (clk, rst_n, ui_in, uo_out);
input clk;
input rst_n;
input [7:0] ui_in;
output [7:0] uo_out;
wire [7:0] uo_out;
reg [7:0] y;
wire b0;
assign b0 = ui_in[0];
reg [7:0] mem [0:3];
always @(posedge clk) begin: memw
    if (rst_n == 0) begin
        mem[0] <= 0;
        mem[1] <= 0;
        mem[2] <= 0;
        mem[3] <= 0;
    end
    else begin
        mem[3] <= (mem[3] + 1);
    end
end
helper u_helper (.clk(clk), .d(b0));
always @(mem[0], mem[1]) begin: pick
    y = mem[3];
end
assign uo_out = y;
endmodule
module helper (clk, d);
input clk;
input d;
reg [7:0] mem [0:1];
always @(posedge clk) begin: hw
    mem[0] <= {7'd0, d};
    mem[1] <= 8'd0;
end
endmodule
