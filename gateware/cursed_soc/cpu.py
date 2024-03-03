from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.cdc import FFSynchronizer
from amaranth.lib.fifo import SyncFIFOBuffered
from amaranth.lib.wiring import In, Out
from amaranth_soc import csr
from .zynq_ifaces import SAxiGP


class Cpu(wiring.Component):
    sys_bus: Out(SAxiGP)
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

    class Clocking(csr.Register, access="rw"):
        reset: csr.Field(csr.action.RW, 1, init=1)
        clock_enable: csr.Field(csr.action.RW, 1)

    class ResetAddress(csr.Register, access="rw"):
        address: csr.Field(csr.action.RW, 32)

    class Debug(csr.Register, access="rw"):
        # 0x : BSCANE2
        # 10 : Host CSRs
        # 11 : External pins
        src_mux: csr.Field(csr.action.RW, 2)
        # 0 : VexRiscv DebugPlugin
        # 1 : Official RISC-V debug
        dst_mux: csr.Field(csr.action.RW, 1)
        tck: csr.Field(csr.action.RW, 1)
        tms: csr.Field(csr.action.RW, 1)
        tdi: csr.Field(csr.action.RW, 1)
        tdo: csr.Field(csr.action.R, 1)

    class GPIO(csr.Register, access="rw"):
        read: csr.Field(csr.action.RW, 32)
        write: csr.Field(csr.action.R, 32)
        write_enable: csr.Field(csr.action.R, 32)

    class MailboxReadStatus(csr.Register, access="r"):
        read_valid: csr.Field(csr.action.R, 1)
        _pad0: csr.Field(csr.action.ResR0WA, 15)
        read_level: csr.Field(csr.action.R, 16)

    class MailboxWriteStatus(csr.Register, access="r"):
        write_ready: csr.Field(csr.action.R, 1)
        _pad1: csr.Field(csr.action.ResR0WA, 15)
        write_level: csr.Field(csr.action.R, 16)

    class MailboxRead(csr.Register, access="r"):
        read_data: csr.Field(csr.action.R, 32)

    class MailboxWrite(csr.Register, access="w"):
        write_data: csr.Field(csr.action.W, 32)

    def __init__(self):
        super().__init__()
        regs = csr.Builder(addr_width=6, data_width=8)

        self._clocking = regs.add("clocking", self.Clocking())
        self._reset_addr = regs.add("reset_addr", self.ResetAddress())
        self._debug = regs.add("debug", self.Debug())
        self._gpio = regs.add("gpio", self.GPIO())
        self._mailbox_read_status = regs.add("mailbox_read_status", self.MailboxReadStatus())
        self._mailbox_write_status = regs.add("mailbox_write_status", self.MailboxWriteStatus())
        self._mailbox_read = regs.add("mailbox_read", self.MailboxRead())
        self._mailbox_write = regs.add("mailbox_write", self.MailboxWrite())

        self._bridge = csr.Bridge(regs.as_memory_map())
        self.csr_bus = self._bridge.bus

    def elaborate(self, platform):
        m = Module()

        with open(__import__("pathlib").Path(__file__).parent.resolve() / "VexRiscvAxi3.v", "r") as f:
            platform.add_file("VexRiscvAxi3.v", f)

        m.submodules.bridge = self._bridge

        m.domains += ClockDomain("cpu_gated", local=True)

        m.submodules.clkbuf_cpu = Instance(
            "BUFGCE",
            i_I=ClockSignal("fclk"),
            i_CE=self._clocking.f.clock_enable.data,
            o_O=ClockSignal("cpu_gated")
        )

        tck = Signal()
        tms = Signal()
        tdi = Signal()
        tdo = Signal()

        bscan_tck = Signal()
        bscan_tms = Signal()
        bscan_tdi = Signal()
        bscan_tdo = Signal()
        m.submodules.bscan = Instance(
            "BSCANE2",
            p_JTAG_CHAIN=4,
            o_TCK=bscan_tck,
            o_TMS=bscan_tms,
            o_TDI=bscan_tdi,
            i_TDO=bscan_tdo,
        )
        platform.add_clock_constraint(tck, 12e6)

        tdo_sync = Signal()
        m.submodules.sync_tdo_csr = FFSynchronizer(tdo, tdo_sync)
        setattr(m.submodules.sync_tdo_csr, "_reset", 0)

        with m.Switch(self._debug.f.src_mux.data):
            with m.Case("0-"):
                m.d.comb += [
                    tck.eq(bscan_tck),
                    tms.eq(bscan_tms),
                    tdi.eq(bscan_tdi),
                    bscan_tdo.eq(tdo),
                ]
            with m.Case("10"):
                m.d.comb += [
                    tck.eq(self._debug.f.tck.data),
                    tms.eq(self._debug.f.tms.data),
                    tdi.eq(self._debug.f.tdi.data),
                    self._debug.f.tdo.r_data.eq(tdo_sync),
                ]
            with m.Case("11"):
                m.d.comb += [
                    tck.eq(self.ext_jtag.tck),
                    tms.eq(self.ext_jtag.tms),
                    tdi.eq(self.ext_jtag.tdi),
                    self.ext_jtag.tdo.eq(tdo),
                ]

        debug_plugin_tck = Signal()
        debug_plugin_tms = Signal()
        debug_plugin_tdi = Signal()
        debug_plugin_tdo = Signal()

        riscv_debug_tck = Signal()
        riscv_debug_tms = Signal()
        riscv_debug_tdi = Signal()
        riscv_debug_tdo = Signal()

        with m.Switch(self._debug.f.dst_mux.data):
            with m.Case("0"):
                m.d.comb += [
                    debug_plugin_tck.eq(tck),
                    debug_plugin_tms.eq(tms),
                    debug_plugin_tdi.eq(tdi),
                    tdo.eq(debug_plugin_tdo),
                ]
            with m.Case("1"):
                m.d.comb += [
                    riscv_debug_tck.eq(tck),
                    riscv_debug_tms.eq(tms),
                    riscv_debug_tdi.eq(tdi),
                    tdo.eq(riscv_debug_tdo),
                ]

        m.submodules.mailbox_fifo_arm_to_riscv = mailbox_fifo_arm_to_riscv = SyncFIFOBuffered(
            width=32,
            depth=16,
        )
        m.submodules.mailbox_fifo_riscv_to_arm = mailbox_fifo_riscv_to_arm = SyncFIFOBuffered(
            width=32,
            depth=16,
        )

        m.d.comb += [
            self._mailbox_read_status.f.read_level.r_data.eq(mailbox_fifo_riscv_to_arm.level),
            self._mailbox_write_status.f.write_level.r_data.eq(mailbox_fifo_arm_to_riscv.level),

            self._mailbox_read_status.f.read_valid.r_data.eq(mailbox_fifo_riscv_to_arm.r_rdy),
            self._mailbox_read.f.read_data.r_data.eq(mailbox_fifo_riscv_to_arm.r_data),
            mailbox_fifo_riscv_to_arm.r_en.eq(self._mailbox_read.f.read_data.r_stb),

            self._mailbox_write_status.f.write_ready.r_data.eq(mailbox_fifo_arm_to_riscv.w_rdy),
            mailbox_fifo_arm_to_riscv.w_data.eq(self._mailbox_write.f.write_data.w_data),
            mailbox_fifo_arm_to_riscv.w_en.eq(self._mailbox_write.f.write_data.w_stb),
        ]

        sys_aw = self.sys_bus.write_address
        sys_w = self.sys_bus.write_data
        sys_b = self.sys_bus.write_response
        sys_ar = self.sys_bus.read_address
        sys_r = self.sys_bus.read
        m.d.comb += self.sys_bus.aclk.eq(ClockSignal("cpu_gated"))

        m.submodules.cpu = Instance(
            "VexRiscvAxi3",
            i_io_rst=self._clocking.f.reset.data,
            i_io_clk=ClockSignal("cpu_gated"),

            i_io_reset_addr=self._reset_addr.f.address.data,

            i_io_jtag_tck=debug_plugin_tck,
            i_io_jtag_tms=debug_plugin_tms,
            i_io_jtag_tdi=debug_plugin_tdi,
            o_io_jtag_tdo=debug_plugin_tdo,

            i_io_riscv_jtag_tck=riscv_debug_tck,
            i_io_riscv_jtag_tms=riscv_debug_tms,
            i_io_riscv_jtag_tdi=riscv_debug_tdi,
            o_io_riscv_jtag_tdo=riscv_debug_tdo,

            i_io_gpio_read=self._gpio.f.read.data,
            o_io_gpio_write=self._gpio.f.write.r_data,
            o_io_gpio_writeEnable=self._gpio.f.write_enable.r_data,

            i_io_mailbox_read_valid=mailbox_fifo_arm_to_riscv.r_rdy,
            o_io_mailbox_read_ready=mailbox_fifo_arm_to_riscv.r_en,
            i_io_mailbox_read_data=mailbox_fifo_arm_to_riscv.r_data,
            o_io_mailbox_write_valid=mailbox_fifo_riscv_to_arm.w_en,
            i_io_mailbox_write_ready=mailbox_fifo_riscv_to_arm.w_rdy,
            o_io_mailbox_write_data=mailbox_fifo_riscv_to_arm.w_data,

            o_io_uart_txd=self.ext_uart.tx,
            i_io_uart_rxd=self.ext_uart.rx,

            o_io_axi3_aw_valid=sys_aw.valid,
            i_io_axi3_aw_ready=sys_aw.ready,
            o_io_axi3_aw_payload_addr=sys_aw.addr,
            o_io_axi3_aw_payload_id=sys_aw.id,
            o_io_axi3_aw_payload_len=sys_aw.len,
            o_io_axi3_aw_payload_size=sys_aw.size,
            o_io_axi3_aw_payload_burst=sys_aw.burst,
            # This breaks memory accesses
            # o_io_axi3_aw_payload_cache=sys_aw.cache,
            # o_io_axi3_aw_payload_qos=sys_aw.qos,
            # o_io_axi3_aw_payload_prot=sys_aw.prot,

            o_io_axi3_w_valid=sys_w.valid,
            i_io_axi3_w_ready=sys_w.ready,
            o_io_axi3_w_payload_data=sys_w.data,
            o_io_axi3_w_payload_strb=sys_w.strb,
            o_io_axi3_w_payload_last=sys_w.last,

            i_io_axi3_b_valid=sys_b.valid,
            o_io_axi3_b_ready=sys_b.ready,
            i_io_axi3_b_payload_id=sys_b.id,
            i_io_axi3_b_payload_resp=sys_b.resp,

            o_io_axi3_ar_valid=sys_ar.valid,
            i_io_axi3_ar_ready=sys_ar.ready,
            o_io_axi3_ar_payload_addr=sys_ar.addr,
            o_io_axi3_ar_payload_id=sys_ar.id,
            o_io_axi3_ar_payload_len=sys_ar.len,
            o_io_axi3_ar_payload_size=sys_ar.size,
            o_io_axi3_ar_payload_burst=sys_ar.burst,
            # This breaks memory accesses
            # o_io_axi3_ar_payload_cache=sys_ar.cache,
            # o_io_axi3_ar_payload_qos=sys_ar.qos,
            # o_io_axi3_ar_payload_prot=sys_ar.prot,

            i_io_axi3_r_valid=sys_r.valid,
            o_io_axi3_r_ready=sys_r.ready,
            i_io_axi3_r_payload_data=sys_r.data,
            i_io_axi3_r_payload_id=sys_r.id,
            i_io_axi3_r_payload_resp=sys_r.resp,
            i_io_axi3_r_payload_last=sys_r.last,

            o_io_axi3_w_id=sys_w.id,
        )

        return m
