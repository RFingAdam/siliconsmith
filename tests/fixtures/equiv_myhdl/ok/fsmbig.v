
`timescale 1ns/1ps

module fsmbig (
    ui_in,
    uo_out,
    uio_out,
    uio_oe,
    clk,
    rst_n
);


input [7:0] ui_in;
output [7:0] uo_out;
reg [7:0] uo_out;
output [7:0] uio_out;
reg [7:0] uio_out;
output [7:0] uio_oe;
reg [7:0] uio_oe;
input clk;
input rst_n;

reg [3:0] state;



always @(posedge clk) begin: fsm
    reg v;
    reg [8-1:0] d;
    if (rst_n == 0) begin
        state <= 4'b0000;
    end
    else begin
        v = ui_in[0];
        d = ui_in[5-1:1];
        case (state)
            4'b0000: begin
                if (v) begin
                    state <= (d == 3) ? 4'b0001 : 4'b0000;
                end
            end
            4'b0001: begin
                if (v) begin
                    state <= (d == 1) ? 4'b0010 : 4'b0000;
                end
            end
            4'b0010: begin
                if (v) begin
                    state <= (d == 4) ? 4'b0011 : 4'b0000;
                end
            end
            4'b0011: begin
                if (v) begin
                    state <= (d == 1) ? 4'b0100 : 4'b0000;
                end
            end
            4'b0100: begin
                if (v) begin
                    state <= (d == 5) ? 4'b0101 : 4'b0000;
                end
            end
            4'b0101: begin
                if (v) begin
                    state <= (d == 9) ? 4'b0110 : 4'b0000;
                end
            end
            4'b0110: begin
                if (v) begin
                    state <= (d == 2) ? 4'b0111 : 4'b0000;
                end
            end
            4'b0111: begin
                if (v) begin
                    state <= (d == 6) ? 4'b1000 : 4'b1001;
                end
            end
            4'b1000: begin
                if (ui_in[5]) begin
                    state <= 4'b0000;
                end
            end
            default: begin
                if (ui_in[6]) begin
                    state <= 4'b0000;
                end
            end
        endcase
    end
end


always @(state) begin: outs
    uo_out = 0;
    case (state)
        4'b1000: begin
            uo_out = 1;
        end
        4'b1001: begin
            uo_out = 2;
        end
    endcase
    uio_out = 0;
    uio_oe = 0;
end

endmodule
