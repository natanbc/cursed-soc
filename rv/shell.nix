{ pkgs ? import (builtins.fetchTarball {
  name = "nixos-23.11-2024-02-25";
  url = "https://github.com/nixos/nixpkgs/archive/73de017ef2d18a04ac4bfd0c02650007ccb31c2a.tar.gz";
  # Hash obtained using `nix-prefetch-url --unpack <url>`
  sha256 = "1v9sy2i2dy3qksx4mf81gwzfl0jzpqccfkzq7fjxgq832f9d255i";
}) {} }:

let
    llvm = pkgs.llvmPackages_15;
in
pkgs.mkShell {
    nativeBuildInputs = with pkgs; [
        gnumake
        llvm.clang-unwrapped
        llvm.bintools-unwrapped
    ];

    shellHook = ''
        unset AS
        unset LD
    '';
}
