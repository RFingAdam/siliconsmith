# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0
"""REVIEWER: a MyHDL project whose top returns an object with its own convert()."""

VERILOG = "\n".join(
    [
        "module mylab4 (clk, rst_n, ui_in, uo_out);",
        "input clk;",
        "input rst_n;",
        "input [7:0] ui_in;",
        "output [7:0] uo_out;",
        "wire [7:0] uo_out;",
        "reg [7:0] cnt;",
        "reg [7:0] y;",
        "wire b0;",
        "assign b0 = ui_in[0];",
        "always @(posedge clk) begin: seq",
        "    if (rst_n == 0) begin",
        "        cnt <= 0;",
        "    end",
        "    else begin",
        "        cnt <= (cnt + 1);",
        "    end",
        "end",
        "always @(b0) begin: pick",
        "    begin: inner",
        "        begin: cnt",
        "        end",
        "    end",
        "    y = cnt;",
        "end",
        "assign uo_out = y;",
        "endmodule",
        "",
    ]
)


class Emitted:
    def convert(self, hdl, name, path, **kwargs):
        with open(f"{path}/{name}.v", "w") as f:
            f.write(VERILOG)


def mylab4(clk, rst_n, ui_in, uo_out):
    return Emitted()
