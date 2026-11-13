
`timescale 1ns/1ps

module seqdet (
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

reg [2:0] state;



always @(posedge clk) begin: fsm
    reg x;
    if (rst_n == 0) begin
        state <= 3'b000;
    end
    else begin
        x = ui_in[0];
        case (state)
            3'b000: begin
                state <= x ? 3'b001 : 3'b000;
            end
            3'b001: begin
                state <= x ? 3'b001 : 3'b010;
            end
            3'b010: begin
                state <= x ? 3'b011 : 3'b000;
            end
            3'b011: begin
                state <= x ? 3'b100 : 3'b010;
            end
            default: begin
                state <= x ? 3'b001 : 3'b010;
            end
        endcase
    end
end



assign uo_out = (state == 3'b100) ? 1 : 0;
assign uio_out = 0;
assign uio_oe = 0;

endmodule
