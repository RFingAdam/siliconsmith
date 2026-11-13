module mloopit (clk, rst_n, ui_in, uo_out);
input clk;
input rst_n;
input [7:0] ui_in;
output [7:0] uo_out;
wire [7:0] uo_out;
reg [7:0] y;
wire b0;
assign b0 = ui_in[0];
reg [7:0] cnt;
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
reg [7:0] t;
always @(cnt, t) begin: pick
    integer i;
    y = 8'd0;
    for (i=0; i<2; i=i+1) begin
        if ((i == 1)) begin
            t = cnt;
        end
        else begin
            y = t;
        end
    end
end
assign uo_out = y;
endmodule
