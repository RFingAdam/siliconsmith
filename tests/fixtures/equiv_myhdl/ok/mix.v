
`timescale 1ns/1ps

module mix (
    clk,
    rst_n,
    ui_in,
    uo_out
);


input clk;
input rst_n;
input [7:0] ui_in;
output [7:0] uo_out;
wire [7:0] uo_out;

reg [1:0] idx;
reg [7:0] o;
reg [7:0] q;
reg [1:0] st;
reg [7:0] w;
reg [7:0] mem [0:4-1];



always @(posedge clk) begin: seq
    integer i;
    if (rst_n == 0) begin
        mem[0] <= 0;
        mem[1] <= 0;
        mem[2] <= 0;
        mem[3] <= 0;
        q <= 0;
        st <= 2'b00;
    end
    else begin
        q <= (q + 1);
        case (st)
            2'b00: begin
                st <= 2'b01;
            end
            2'b01: begin
                st <= 2'b10;
            end
            default: begin
                st <= 2'b00;
            end
        endcase
        for (i=0; i<4; i=i+1) begin
            mem[i] <= ($signed({1'b0, q}) + i);
        end
    end
end


always @(idx, ui_in, q, mem[0], mem[1], mem[2], mem[3], w, st) begin: comb
    reg [8-1:0] v;
    reg x;
    reg y;
    idx = ui_in[2-1:0];
    v = 8'h0;
    x = (ui_in[0] && q[1]);
    y = (q[2] || ui_in[1]);
    if (x) begin
        v = q;
    end
    else begin
        v = ui_in;
    end
    v[4-1:2] = ui_in[2-1:0];
    case (st)
        2'b00: begin
            w = (v + mem[idx]);
        end
        2'b01: begin
            w = {v[4-1:0], q[8-1:4]};
        end
        default: begin
            w = ((v << 1) | y);
        end
    endcase
    if (ui_in[7]) begin
        o = (w >>> 2);
    end
    else begin
        o = (~w);
    end
end



assign uo_out = o;

endmodule
