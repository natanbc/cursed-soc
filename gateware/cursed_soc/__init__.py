from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out
from amaranth_soc import wishbone
from amaranth_soc.csr.wishbone import WishboneCSRBridge
from .axi_to_wishbone import Axi2Wishbone
from .cpu import Cpu
from .zynq_ifaces import MAxiGP, SAxiGP


__all__ = ["SoC"]


class SoC(wiring.Component):
    ext_jtag: In(wiring.Signature({
        "tck": Out(1),
        "tms": Out(1),
        "tdi": Out(1),
        "tdo": In(1),
    }))
    ext_uart: Out(wiring.Signature({
        "tx": Out(1),
        "rx": In(1),
    }))
    cpu_to_sys: Out(SAxiGP)
    sys_to_csr: Out(MAxiGP)

    def __init__(self):
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        m.submodules.cpu = cpu = Cpu()
        wiring.connect(m, cpu.ext_jtag, wiring.flipped(self.ext_jtag))
        wiring.connect(m, cpu.ext_uart, wiring.flipped(self.ext_uart))
        wiring.connect(m, cpu.sys_bus, wiring.flipped(self.cpu_to_sys))

        m.submodules.axi2wb = axi2wb = Axi2Wishbone()
        wiring.connect(m, axi2wb.axi, wiring.flipped(self.sys_to_csr))
        m.submodules.decoder = decoder = wishbone.Decoder(
            addr_width=30,
            data_width=32,
            granularity=8,
            features={"err"},
        )

        m.submodules.csr_wb = csr_wb = WishboneCSRBridge(cpu.csr_bus, data_width=32)

        decoder.add(csr_wb.wb_bus, addr=0x4000_0000)
        wiring.connect(m, axi2wb.wishbone, decoder.bus)

        for resource in decoder.bus.memory_map.all_resources():
            name = "_".join(i if isinstance(i, str) else "_".join(i) for i in resource.path)
            address = resource.start
            print(f"{name} @ 0x{address:08x}-0x{resource.end:08x}")

        return m
