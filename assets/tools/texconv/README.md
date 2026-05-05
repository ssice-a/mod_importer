# Bundled texconv

This folder contains Microsoft's DirectXTex `texconv.exe` portable texture
converter used by Mod Importer for DDS preview/export conversion.

- Source: https://github.com/microsoft/DirectXTex
- Tool docs: https://github.com/microsoft/DirectXTex/wiki/Texconv
- License: MIT, see `LICENSE-DirectXTex.txt`

The add-on resolves the converter in this order:

1. `MODIMP_TEXCONV` environment variable.
2. `assets/tools/texconv/texconv.exe`.
3. `texconv.exe` from `PATH`.
