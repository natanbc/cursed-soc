use std::fs::OpenOptions;
use std::io::Error;
use std::os::fd::IntoRawFd;
use std::os::unix::fs::OpenOptionsExt;
use std::ptr::{addr_of, addr_of_mut};
use crate::hal::MemoryMap;

#[repr(C)]
struct ClockingRegister {
    bits: u32,
}

#[repr(C)]
struct ResetAddr {
    addr: u32,
}

#[repr(C)]
struct Gpio {
    read: u32,
    write: u32,
    write_enable: u32,
    _pad0: u32,
}

#[repr(C)]
struct MailboxReadStatus {
    valid: u16,
    level: u16,
}

#[repr(C)]
struct MailboxWriteStatus {
    ready: u16,
    level: u16,
}

#[repr(C)]
struct MailboxRead {
    read_data: u32,
}

#[repr(C)]
struct MailboxWrite {
    write_data: u32,
}

#[repr(C)]
struct CpuRegisters {
    clocking: ClockingRegister,
    reset_addr: ResetAddr,
    debug: u32,
    _pad0: u32,
    gpio: Gpio,
    mailbox_read_status: MailboxReadStatus,
    mailbox_write_status: MailboxWriteStatus,
    mailbox_read: MailboxRead,
    mailbox_write: MailboxWrite,
}

pub struct Cpu {
    map: MemoryMap,
}

impl Cpu {
    pub unsafe fn open() -> std::io::Result<Self> {
        let f = OpenOptions::new().read(true).write(true)
            .custom_flags(libc::O_SYNC).open("/dev/mem")?;

        let res = libc::mmap(
            core::ptr::null_mut(),
            4096,
            libc::PROT_READ|libc::PROT_WRITE,
            libc::MAP_SHARED,
            f.into_raw_fd(),
            0x4000_0000,
        );
        if res == libc::MAP_FAILED {
            return Err(Error::last_os_error());
        }

        return Ok(Self {
            map: MemoryMap {
                ptr: res,
                size: 4096,
            }
        })
    }

    pub unsafe fn boot(&mut self, code_addr: usize) {
        self.kill();
        let regs = self.regs_mut();
        addr_of_mut!((*regs).reset_addr).write_volatile(ResetAddr {
            addr: code_addr as u32,
        });
        //RESET + CLOCK_ENABLE
        addr_of_mut!((*regs).clocking).write_volatile(ClockingRegister {
            bits: 0b11,
        });
        //CLOCK_ENABLE
        addr_of_mut!((*regs).clocking).write_volatile(ClockingRegister {
            bits: 0b10,
        });
    }

    pub unsafe fn kill(&mut self) {
        let regs = self.regs_mut();
        //RESET
        addr_of_mut!((*regs).clocking).write_volatile(ClockingRegister {
            bits: 0b01,
        });
    }

    pub fn mailbox_send(&mut self, val: u32) {
        let regs = self.regs_mut();
        unsafe {
            loop {
                let ready = addr_of!((*regs).mailbox_write_status).read_volatile().ready;
                if ready != 0 {
                    break;
                }
                std::thread::yield_now();
            }
            addr_of_mut!((*regs).mailbox_write).write_volatile(MailboxWrite {
                write_data: val,
            });
        }
    }

    pub fn mailbox_recv(&mut self) -> u32 {
        let regs = self.regs_mut();
        unsafe {
            loop {
                let valid = addr_of!((*regs).mailbox_read_status).read_volatile().valid;
                if valid != 0 {
                    break;
                }
                std::thread::yield_now();
            }
            addr_of!((*regs).mailbox_read).read_volatile().read_data
        }
    }

    fn regs(&self) -> *const CpuRegisters {
        *self.map as *const CpuRegisters
    }

    fn regs_mut(&mut self) -> *mut CpuRegisters {
        *self.map as *mut CpuRegisters
    }
}

impl Drop for Cpu {
    fn drop(&mut self) {
        unsafe {
            self.kill();
        }
    }
}
