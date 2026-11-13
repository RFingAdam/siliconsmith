
`timescale 1ns/1ps

module fsmoh (
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

reg [9:0] state;



always @(posedge clk) begin: fsm
    reg v;
    reg [8-1:0] d;
    if (rst_n == 0) begin
        state <= 10'b0000000001;
    end
    else begin
        v = ui_in[0];
        d = ui_in[5-1:1];
        case (state)
            10'b0000000001: begin
                if (v) begin
                    state <= (d == 3) ? 10'b0000000010 : 10'b0000000001;
                end
            end
            10'b0000000010: begin
                if (v) begin
                    state <= (d == 1) ? 10'b0000000100 : 10'b0000000001;
                end
            end
            10'b0000000100: begin
                if (v) begin
                    state <= (d == 4) ? 10'b0000001000 : 10'b0000000001;
                end
            end
            10'b0000001000: begin
                if (v) begin
                    state <= (d == 1) ? 10'b0000010000 : 10'b0000000001;
                end
            end
            10'b0000010000: begin
                if (v) begin
                    state <= (d == 5) ? 10'b0000100000 : 10'b0000000001;
                end
            end
            10'b0000100000: begin
                if (v) begin
                    state <= (d == 9) ? 10'b0001000000 : 10'b0000000001;
                end
            end
            10'b0001000000: begin
                if (v) begin
                    state <= (d == 2) ? 10'b0010000000 : 10'b0000000001;
                end
            end
            10'b0010000000: begin
                if (v) begin
                    state <= (d == 6) ? 10'b0100000000 : 10'b1000000000;
                end
            end
            10'b0100000000: begin
                if (ui_in[5]) begin
                    state <= 10'b0000000001;
                end
            end
            default: begin
                if (ui_in[6]) begin
                    state <= 10'b0000000001;
                end
            end
        endcase
    end
end


always @(state) begin: outs
    uo_out = 0;
    case (state)
        10'b0100000000: begin
            uo_out = 1;
        end
        10'b1000000000: begin
            uo_out = 2;
        end
    endcase
    uio_out = 0;
    uio_oe = 0;
end

endmodule
