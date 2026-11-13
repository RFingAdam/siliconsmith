
`timescale 1ns/1ps

module lfsr32 (
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

reg [31:0] lfsr;



always @(posedge clk) begin: seq
    if (rst_n == 0) begin
        lfsr <= 1;
    end
    else begin
        if (ui_in[0]) begin
            lfsr <= {lfsr[31-1:0], (((lfsr[31] ^ lfsr[21]) ^ lfsr[1]) ^ lfsr[0])};
        end
    end
end



assign uo_out = {7'h0, lfsr[0]};
assign uio_out = 0;
assign uio_oe = 0;

endmodule
