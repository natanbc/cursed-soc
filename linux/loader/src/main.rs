use std::os::unix::prelude::ExitStatusExt;
use std::sync::{Arc, Mutex};
use elf::abi::PT_LOAD;
use elf::endian::LittleEndian;
use elf::file::Class;
use log::{debug, info, trace};

mod hal;

use clap::Parser;

/// ELF loader for FPGA-size RISC-V core
#[derive(Parser, Debug)]
#[command(version, about, long_about = None)]
struct Args {
    /// ELF file to load
    #[arg()]
    path: String,

    /// Program to run on the host to interact with the CPU.
    #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
    args: Vec<String>,
}

const RELOCATION_NAMES: &[&'static str] = &[
    "R_RISCV_NONE",
    "R_RISCV_32",
    "R_RISCV_64",
    "R_RISCV_RELATIVE",
    "R_RISCV_COPY",
    "R_RISCV_JUMP_SLOT",
    "R_RISCV_TLS_DTPMOD32",
    "R_RISCV_TLS_DTPMOD64",
    "R_RISCV_TLS_DTPREL32",
    "R_RISCV_TLS_DTPREL64",
    "R_RISCV_TLS_TPREL32",
    "R_RISCV_TLS_TPREL64",
    "UNKNOWN",
    "UNKNOWN",
    "UNKNOWN",
    "UNKNOWN",
    "R_RISCV_BRANCH",
    "R_RISCV_JAL",
    "R_RISCV_CALL",
    "R_RISCV_CALL_PLT",
    "R_RISCV_GOT_HI20",
    "R_RISCV_TLS_GOT_HI20",
    "R_RISCV_TLS_GD_HI20",
    "R_RISCV_PCREL_HI20",
    "R_RISCV_PCREL_LO12_I",
    "R_RISCV_PCREL_LO12_S",
    "R_RISCV_HI20",
    "R_RISCV_LO12_I",
    "R_RISCV_LO12_S",
    "R_RISCV_TPREL_HI20",
    "R_RISCV_TPREL_LO12_I",
    "R_RISCV_TPREL_LO12_S",
    "R_RISCV_TPREL_ADD",
    "R_RISCV_ADD8",
    "R_RISCV_ADD16",
    "R_RISCV_ADD32",
    "R_RISCV_ADD64",
    "R_RISCV_SUB8",
    "R_RISCV_SUB16",
    "R_RISCV_SUB32",
    "R_RISCV_SUB64",
    "UNKNOWN",
    "UNKNOWN",
    "R_RISCV_ALIGN",
    "R_RISCV_RVC_BRANCH",
    "R_RISCV_RVC_JUMP",
    "R_RISCV_RVC_LUI",
    "UNKNOWN",
    "UNKNOWN",
    "UNKNOWN",
    "UNKNOWN",
    "R_RISCV_RELAX",
    "R_RISCV_SUB6",
    "R_RISCV_SET6",
    "R_RISCV_SET8",
    "R_RISCV_SET16",
    "R_RISCV_SET32",
    "R_RISCV_32_PCREL",
    "R_RISCV_IRELATIVE",
];

fn riscv_rel_name(r_type: u32) -> &'static str {
    RELOCATION_NAMES.get(r_type as usize).unwrap_or(&"UNKNOWN")
}

fn main() {
    env_logger::init();
    let args = Args::parse();

    let data = std::fs::read(&args.path).expect("Failed to read ELF file");
    let elf = elf::ElfBytes::<LittleEndian>::minimal_parse(&data[..]).expect("Failed to parse ELF file");
    info!("Loading program {}", args.path);

    assert_eq!(elf.ehdr.e_machine, elf::abi::EM_RISCV, "Invalid ELF E_MACHINE");
    assert_eq!(elf.ehdr.class, Class::ELF32, "Invalid ELF class");
    // assert_eq!(elf.ehdr.e_type, elf::abi::ET_EXEC, "Invalid ELF type");


    let mut min_vaddr = u64::MAX;
    let mut max_vaddr = 0;
    for p in elf.segments().unwrap()
        .iter()
        .filter(|phdr|{phdr.p_type == PT_LOAD}) {
        min_vaddr = min_vaddr.min(p.p_vaddr);
        max_vaddr = max_vaddr.max(p.p_vaddr + p.p_memsz);
        debug!("PT_LOAD vaddr=0x{:08X}, size=0x{:X}, type={}, flags=0x{:X}", p.p_vaddr, p.p_memsz, p.p_type, p.p_flags)
    }
    info!("Allocating {} bytes of physical memory for the binary", max_vaddr - min_vaddr);
    let mem_size = (max_vaddr - min_vaddr) as usize;
    let alloc_size = (mem_size + 4095) / 4096 * 4096;

    let alloc = hal::Userdma::open().expect("userdma driver not loaded");
    let (mut code_buf, load_addr) = alloc.alloc_buf(alloc_size).expect("Failed to allocate buffer");
    debug!("Allocated {alloc_size} bytes at 0x{load_addr:08X}");

    {
        let base_map = code_buf.map().unwrap();
        let base = *base_map as *mut u8;
        code_buf.with_sync(|| {
            for p in elf.segments().unwrap()
                .iter()
                .filter(|phdr|{phdr.p_type == PT_LOAD}) {
                let offset = (p.p_vaddr - min_vaddr) as u32;
                debug!("Loading segment 0x{:08X}", p.p_vaddr);
                unsafe {
                    core::ptr::copy_nonoverlapping(
                        data[p.p_offset as usize..].as_ptr(),
                        base.add(offset as usize),
                        p.p_filesz as usize,
                    )
                }
            }

            let mut syms = Vec::new();
            let mut string_table = None;
            if let Some((symbol_table, strings)) = elf.symbol_table().unwrap() {
                for sym in symbol_table {
                    syms.push((sym.st_value, sym.st_name));
                }
                string_table = Some(strings);
            }
            let syms = syms;

            let process_relocation = move |r_type: u32, r_sym: u32, r_offset: u64, r_addend: i64| {
                trace!("Resolving RELA {} at 0x{:X} to sym({}) + {}", riscv_rel_name(r_type), r_offset, r_sym, r_addend);
                let (sym_virt, sym_name) = *syms.get(r_sym as usize).expect("Symbol not found");
                debug!(
                    "Resolving RELA {} at 0x{:X} to sym({})+{}",
                    riscv_rel_name(r_type),
                    r_offset,
                    string_table.as_ref().map(|s| s.get(sym_name as usize).unwrap()).unwrap_or("UNK"),
                    r_addend
                );
                let symbol_phys = load_addr + (
                    (sym_virt - min_vaddr) as i64 + r_addend
                ) as usize;

                let pc = (load_addr + (r_offset - min_vaddr) as usize) as u32;
                let symbol_phys = symbol_phys as u32;
                let inst = unsafe {
                    base.add((r_offset - min_vaddr) as usize) as *mut u32
                };
                match r_type {
                    elf::abi::R_RISCV_HI20 => {
                        let sym = symbol_phys + 0x800;
                        unsafe {
                            inst.write((inst.read() & 0xFFF) | (sym & 0xFFFFF000));
                        }
                    },
                    elf::abi::R_RISCV_LO12_I => {
                        unsafe {
                            inst.write((inst.read() & !0xFFF0_0000) | ((symbol_phys & 0xFFF) << 20));
                        }
                    },
                    elf::abi::R_RISCV_CALL|elf::abi::R_RISCV_CALL_PLT => {
                        let diff = symbol_phys.wrapping_sub(pc);
                        unsafe {
                            inst.write((inst.read() & 0xFFF) | (diff & 0xFFFFF000));
                        }
                        unsafe {
                            let inst = inst.add(1);
                            inst.write((inst.read() & !0xFFF0_0000) | ((diff & 0xFFF) << 20));
                        }
                    },
                    _ => {
                        panic!("Unsupported relocation type {}", riscv_rel_name(r_type));
                    }
                }
            };

            if let Some(rel_hdr) = elf.section_header_by_name(".rel.text").unwrap() {
                for rela in elf.section_data_as_rels(&rel_hdr).unwrap() {
                    process_relocation(rela.r_type, rela.r_sym, rela.r_offset, 0);
                }
            }

            if let Some(rela_hdr) = elf.section_header_by_name(".rela.text").unwrap() {
                for rela in elf.section_data_as_relas(&rela_hdr).unwrap() {
                    process_relocation(rela.r_type, rela.r_sym, rela.r_offset, rela.r_addend);
                }
            }
        });
    }

    let mut cpu = unsafe {
        hal::Cpu::open().unwrap()
    };
    let start = load_addr + (elf.ehdr.e_entry - min_vaddr) as usize;
    info!("Starting CPU at vaddr 0x{:08X} (phys 0x{:08x})", elf.ehdr.e_entry, start);
    unsafe {
        cpu.boot(start);
    }

    struct KillCpuOnDrop {
        c: hal::Cpu,
    }
    impl Drop for KillCpuOnDrop {
        fn drop(&mut self) {
            unsafe {
                self.c.kill();
            }
        }
    }

    let cpu = Arc::new(Mutex::new(Some(KillCpuOnDrop {
        c: cpu,
    })));
    ctrlc::set_handler({
        let cpu = Arc::clone(&cpu);
        move || {
            cpu.lock().unwrap().take();
            std::process::exit(0);
        }
    }).expect("Error setting Ctrl-C handler");


    if args.args.is_empty() {
        loop {
            std::thread::sleep(std::time::Duration::from_secs(100));
        }
    } else {
        let res = std::process::Command::new(&args.args[0])
            .args(&args.args[1..])
            .spawn().unwrap().wait().unwrap();
        //Kill CPU
        cpu.lock().unwrap().take();
        std::process::exit(res.into_raw());
    }
}
