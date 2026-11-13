module mycase2 (clk, rst_n, ui_in, uo_out);
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
always @(b0) begin: pick
    case (1'b1)
        b0: y = 8'd3;
        cnt <= 8'd100: y = 8'd1;
        default: y = 8'd2;
    endcase
end
assign uo_out = y;
endmodule
