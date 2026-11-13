
`timescale 1ns/1ps

module shiftpar (
    ui_in,
    uo_out,
    uio_out,
    uio_oe,
    clk,
    rst_n
);


input [7:0] ui_in;
output [7:0] uo_out;
wire [7:0] uo_out;
output [7:0] uio_out;
wire [7:0] uio_out;
output [7:0] uio_oe;
wire [7:0] uio_oe;
input clk;
input rst_n;

wire par;
reg [15:0] sr;



always @(posedge clk) begin: seq
    if (rst_n == 0) begin
        sr <= 0;
    end
    else begin
        sr <= {sr[15-1:0], ui_in[0]};
    end
end



assign par = (((((((((((((((sr[0] ^ sr[1]) ^ sr[2]) ^ sr[3]) ^ sr[4]) ^ sr[5]) ^ sr[6]) ^ sr[7]) ^ sr[8]) ^ sr[9]) ^ sr[10]) ^ sr[11]) ^ sr[12]) ^ sr[13]) ^ sr[14]) ^ sr[15]);



assign uo_out = {7'h0, par};
assign uio_out = 0;
assign uio_oe = 0;

endmodule
