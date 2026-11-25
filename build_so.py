#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Python -> Native .so Compiler (Cython) for Termux & Linux
Compiles all .py modules in any given folder into native .so binaries
so you can share the dist_compiled/ folder without exposing source code.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile


def _check_tools():
    missing = []
    for mod in ("Cython", "setuptools"):
        try:
            __import__(mod)
        except ImportError:
            missing.append("cython" if mod == "Cython" else "setuptools")

    has_cc = bool(shutil.which("clang") or shutil.which("gcc") or shutil.which("cc"))
    if missing or not has_cc:
        print("\n\033[91m[!] Compiler dependencies missing:\033[0m")
        if not has_cc:
            print("    - C compiler (clang / gcc) not found")
        if missing:
            print(f"    - Python packages missing: {', '.join(missing)}")
        print("\n\033[93m[i] Termux par ye command run karo:\033[0m")
        print("    pkg update -y && pkg install -y python clang && pip install cython setuptools\n")
        sys.exit(1)


def _setup_cc_env():
    env = os.environ.copy()
    if "CC" not in env:
        if shutil.which("clang"):
            env["CC"] = "clang"
        elif shutil.which("gcc"):
            env["CC"] = "gcc"
    return env


def compile_folder(src_dir: str, out_dir: str, entry_name: str = ""):
    _check_tools()
    src_dir = os.path.abspath(src_dir)
    out_dir = os.path.abspath(out_dir)

    if not os.path.isdir(src_dir):
        raise RuntimeError(f"Source directory not found: {src_dir}")

    skip_names = {os.path.basename(__file__), "setup.py", "start.py", "run.py"}
    py_files = sorted(
        f for f in os.listdir(src_dir)
        if f.endswith(".py") and f not in skip_names and not f.startswith("__")
    )
    if not py_files:
        raise RuntimeError(f"No .py files found in {src_dir}")

    print(f"\n\033[96m[i] Source Folder:\033[0m {src_dir}")
    print(f"\033[96m[i] Output Folder:\033[0m {out_dir}")
    print(f"\033[96m[i] Modules to compile ({len(py_files)}):\033[0m {', '.join(py_files)}\n")

    os.makedirs(out_dir, exist_ok=True)
    env = _setup_cc_env()

    with tempfile.TemporaryDirectory(prefix="cy_build_") as tmp:
        # Copy .py files into isolated temp workspace so original folder stays clean
        for fname in py_files:
            shutil.copy2(os.path.join(src_dir, fname), os.path.join(tmp, fname))

        module_list_repr = repr([os.path.splitext(f)[0] for f in py_files])
        setup_code = f"""from setuptools import setup, Extension
from Cython.Build import cythonize

mods = {module_list_repr}
exts = [Extension(m, [m + ".py"], extra_compile_args=["-O3"]) for m in mods]
setup(
    name="compiled_bundle",
    ext_modules=cythonize(
        exts,
        compiler_directives={{
            "language_level": "3",
            "embedsignature": False,
            "binding": False,
            "docstrings": False,
        }},
        nthreads=2,
    ),
)
"""
        setup_path = os.path.join(tmp, "setup.py")
        with open(setup_path, "w", encoding="utf-8") as f:
            f.write(setup_code)

        print("\033[93m[*] Compiling Python -> C -> .so binaries...\033[0m", flush=True)
        proc = subprocess.run(
            [sys.executable, setup_path, "build_ext", "--inplace"],
            cwd=tmp,
            env=env,
        )
        if proc.returncode != 0:
            raise RuntimeError("Cython compilation failed (see compiler output above)")

        # Collect compiled .so files and strip symbols if strip tool exists
        so_files = []
        strip_bin = shutil.which("strip")
        for fname in sorted(os.listdir(tmp)):
            if fname.endswith(".so"):
                src_so = os.path.join(tmp, fname)
                dst_so = os.path.join(out_dir, fname)
                shutil.copy2(src_so, dst_so)
                if strip_bin:
                    subprocess.run([strip_bin, "--strip-unneeded", dst_so], stderr=subprocess.DEVNULL)
                so_files.append(fname)

        if not so_files:
            raise RuntimeError("No .so files were produced")

    # Copy runtime config/data files (json, txt) without copying any .py source
    copied_configs = []
    for fname in sorted(os.listdir(src_dir)):
        if fname.endswith((".json", ".txt")) and os.path.isfile(os.path.join(src_dir, fname)):
            shutil.copy2(os.path.join(src_dir, fname), os.path.join(out_dir, fname))
            copied_configs.append(fname)

    # Determine entrypoint module for tiny start.py launcher
    mod_names = [os.path.splitext(f)[0] for f in py_files]
    if entry_name:
        entry_mod = os.path.splitext(entry_name)[0]
    elif "guild_glory" in mod_names:
        entry_mod = "guild_glory"
    elif "main" in mod_names:
        entry_mod = "main"
    else:
        entry_mod = mod_names[0]

    launcher_path = os.path.join(out_dir, "start.py")
    launcher_code = (
        f"import {entry_mod}\n\n"
        f'if __name__ == "__main__":\n'
        f"    if hasattr({entry_mod}, 'main'):\n"
        f"        {entry_mod}.main()\n"
    )
    with open(launcher_path, "w", encoding="utf-8") as f:
        f.write(launcher_code)

    print("\n\033[92m[✓] Compilation Successful! (.py source NOT included in output)\033[0m")
    print(f"    Compiled .so files : {', '.join(so_files)}")
    if copied_configs:
        print(f"    Copied configs     : {', '.join(copied_configs)}")
    print(f"    Launcher           : start.py (calls {entry_mod}.main())")
    print(f"\n\033[96m[i] Share karne ke liye sirf ye folder do:\033[0m {out_dir}")
    print(f"\033[96m[i] Run command:\033[0m cd {out_dir} && python start.py\n")


def main():
    ap = argparse.ArgumentParser(description="Compile Python source folder into .so binaries (Cython)")
    ap.add_argument("--path", default="", help="Source folder path containing .py files")
    ap.add_argument("--out", default="", help="Output folder path (default: <path>/dist_compiled)")
    ap.add_argument("--entry", default="", help="Main module name (default: guild_glory or main)")
    args = ap.parse_args()

    print("\033[96m" + "=" * 56)
    print("   PYTHON -> .SO COMPILER (CYTHON PROTECTOR)")
    print("=" * 56 + "\033[0m")

    src_dir = args.path.strip()
    if not src_dir:
        default_dir = os.path.dirname(os.path.abspath(__file__))
        raw = input(f"\n\033[96m[+] Source folder path daalo (Enter = {default_dir}): \033[0m").strip()
        src_dir = raw or default_dir

    out_dir = args.out.strip() or os.path.join(os.path.abspath(src_dir), "dist_compiled")

    try:
        compile_folder(src_dir, out_dir, entry_name=args.entry.strip())
    except KeyboardInterrupt:
        print("\nCancelled.")
    except Exception as e:
        print(f"\n\033[91m[-] Error: {e}\033[0m\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
