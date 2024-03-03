#![no_std]

use core::panic::PanicInfo;
use core::ptr::{addr_of, addr_of_mut};

#[repr(C)]
struct Uart {
    data: u32,
    status: u32,
    clock_divider: u32,
    frame_config: u32,
}

#[export_name="_start"]
extern "C" fn main() {
    let uart = 0xF000_2000 as *mut Uart;
    unsafe {
        addr_of_mut!((*uart).clock_divider).write_volatile(107);
        addr_of_mut!((*uart).frame_config).write_volatile(7);
        let write_char = move |c: u8| {
            loop {
                let s = addr_of!((*uart).status).read_volatile();
                let wa = (s >> 16) & 0xFF;
                if wa != 0 {
                    break;
                }
            }
            addr_of_mut!((*uart).data).write_volatile(c as u32);
        };
        loop {
            for b in b"Hello World!\r\n" {
                write_char(*b);
            }
            for _ in 0..10000000 {
                addr_of!((*uart).status).read_volatile();
            }
        }
    }
}

#[panic_handler]
fn lol(_: &PanicInfo) -> ! {
    loop {}
}