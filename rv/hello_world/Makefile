hello_world.elf: Cargo.toml Cargo.lock .cargo/config.toml src/lib.rs
	cargo build --release
	nix-shell ../shell.nix --run 'ld.lld target/riscv32im-unknown-none-elf/release/libhello_world.a -o hello_world.elf --nmagic --emit-relocs'