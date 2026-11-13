module myarr (clk, rst_n, ui_in, uo_out);
input clk;
input rst_n;
input [7:0] ui_in;
output [7:0] uo_out;
wire [7:0] uo_out;
reg [7:0] cnt;
reg [7:0] y;
wire b0;
assign b0 = ui_in[0];
always @(posedge clk) begin: seq
    if (rst_n == 0) begin
        cnt <= 0;
    end
    else begin
        cnt <= (cnt + 1);
    end
end
reg [0:7] v;
wire [0:3] w;
always @(posedge clk) begin: seqv
    v <= cnt;
end
assign w = v[0:3];
always @(v[0], v[1], v[2], v[3]) begin: pick
    y = {7'd0, v[5]};
end
assign uo_out = y;
endmodule
