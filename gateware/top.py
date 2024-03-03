from amaranth import *
from amaranth.lib import wiring
from amaranth.build import *
from board import ebaz4205
from cursed_soc import SoC
from cursed_soc.ps7 import PS7


class Top(Elaboratable):
    def elaborate(self, platform):
        m = Module()

        m.submodules.ps7 = ps7 = PS7()
        m.submodules.soc = soc = SoC()

        m.domains += ClockDomain("fclk", local=True)
        clk, rst = ps7.fclk(0, 100e6, raw=True)
        m.d.comb += [
            ClockSignal("fclk").eq(clk),
            ResetSignal("fclk").eq(rst),
        ]

        m.domains += ClockDomain("sync", local=True)
        m.d.comb += ResetSignal("sync").eq(ResetSignal("fclk"))
        m.submodules.clkbuf_sync = Instance(
            "BUFG",
            i_I=ClockSignal("fclk"),
            o_O=ClockSignal("sync"),
        )

        wiring.connect(m, soc.cpu_to_sys, wiring.flipped(ps7.axi_gp_s()))
        wiring.connect(m, soc.sys_to_csr, wiring.flipped(ps7.axi_gp_m(0)))

        uart = platform.request("uart", 0)
        m.d.comb += [
            uart.tx.o.eq(soc.ext_uart.tx),
            soc.ext_uart.rx.eq(uart.rx.i),
        ]

        platform.add_resources([Resource(
            "jtag", 0,
            Subsignal("tck", Pins("2", conn=("h", 4), dir="i")),
            Subsignal("tms", Pins("4", conn=("h", 4), dir="i")),
            Subsignal("tdi", Pins("6", conn=("h", 4), dir="i")),
            Subsignal("tdo", Pins("8", conn=("h", 4), dir="o")),
            Attrs(IOSTANDARD="LVCMOS33")
        )])
        jtag = platform.request("jtag", 0)
        m.d.comb += [
            soc.ext_jtag.tck.eq(jtag.tck.i),
            soc.ext_jtag.tms.eq(jtag.tms.i),
            soc.ext_jtag.tdi.eq(jtag.tdi.i),
            jtag.tdo.o.eq(soc.ext_jtag.tdo),
        ]

        enet_pins = platform.request("enet", 0)
        enet = ps7.emio_enet(0)
        m.d.comb += [
            enet_pins.tx_data.o.eq(enet.gmii_txd),
            enet.gmii_rxd.eq(enet_pins.rx_data.i),
            enet_pins.tx_en.o.eq(enet.gmii_tx_en),
            enet.gmii_rx_dv.eq(enet_pins.rx_dv.i),
            enet.gmii_tx_clk.eq(enet_pins.tx_clk.i),
            enet.gmii_rx_clk.eq(enet_pins.rx_clk.i),
            enet_pins.mdc.o.eq(enet.mdio_mdc),
            enet_pins.mdio.o.eq(enet.mdio_o),
            enet.mdio_i.eq(enet_pins.mdio.i),
            enet_pins.mdio.oe.eq(enet.mdio_oe),
        ]

        return m


plat = ebaz4205.EBAZ4205Platform().with_extension_board(True)
plat.build(Top(), do_build=True, do_program=True, add_constraints="""
set_property BITSTREAM.GENERAL.COMPRESS true [current_design]
set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets {pin_enet_0__tx_clk/sigs__gmii_tx_clk}]
""", script_after_read="""
set_param general.maxThreads 12
auto_detect_xpm
""", script_after_bitstream="""
write_cfgmem -force -format bin -interface smapx32 -disablebitswap -loadbit "up 0 top.bit" top.swap.bin
""")
