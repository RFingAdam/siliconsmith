module myfst (ui_in, uo_out);
input [7:0] ui_in;
output [7:0] uo_out;
wire [7:0] uo_out;
function [7:0] f;
  input [7:0] a;
  reg [7:0] keep;
  begin
    if (a[0]) keep = a;
    f = keep;
  end
endfunction
assign uo_out = f(ui_in);
endmodule
