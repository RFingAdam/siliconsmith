
`timescale 1ns/1ps

module combo (
    ui_in,
    uo_out,
    uio_in,
    uio_out,
    uio_oe
);


input [7:0] ui_in;
output [7:0] uo_out;
wire [7:0] uo_out;
input [7:0] uio_in;
output [7:0] uio_out;
wire [7:0] uio_out;
output [7:0] uio_oe;
wire [7:0] uio_oe;





assign uo_out = ((ui_in + uio_in) % 256);
assign uio_out = ((ui_in ^ (~uio_in)) & 255);
assign uio_oe = 240;

endmodule
