module agenif (clk, rst_n, ui_in, uo_out);
input clk;
input rst_n;
input [7:0] ui_in;
output [7:0] uo_out;
wire [7:0] uo_out;
reg [7:0] y;
wire b0;
assign b0 = ui_in[0];
reg [7:0] mem;
always @(posedge clk) begin: seq
    if (rst_n == 0) begin
        mem <= 0;
    end
    else begin
        mem <= (mem + 4);
    end
end
/* verilator lint_off GENUNNAMED */
/* verilator lint_off VARHIDDEN */
generate if (1) reg [7:0] mem [0:1]; endgenerate
always @(mem[0], mem[1]) begin: pick
    y = mem;
end
assign uo_out = y;
endmodule
