
`timescale 1ns/1ps

module counter (
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

reg [7:0] count;



always @(posedge clk) begin: seq
    if (rst_n == 0) begin
        count <= 0;
    end
    else begin
        if (ui_in[0]) begin
            count <= (count + 1);
        end
    end
end



assign uo_out = count;
assign uio_out = 0;
assign uio_oe = 0;

endmodule
