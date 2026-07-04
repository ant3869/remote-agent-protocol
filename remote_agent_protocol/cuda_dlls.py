"""Windows CUDA DLL bootstrap for faster-whisper / ctranslate2.

The GPU Whisper backend (ctranslate2) needs cuBLAS + cuDNN + the CUDA runtime
DLLs at inference time. We install those as pip wheels (nvidia-*-cu12), but on
Windows their `bin` folders inside site-packages aren't on the DLL search path,
so ctranslate2 fails with "cublas64_12.dll ... cannot be loaded".

Importing this module fixes that: it finds every `nvidia/*/bin` folder in the
active environment and registers it (both os.add_dll_directory AND a PATH
prepend, because ctranslate2 loads DLLs from its own C++ code which respects
PATH more reliably). Import it BEFORE faster_whisper / the Whisper STT service.
"""

import glob
import os
import sysconfig

from loguru import logger

_DONE = False


def setup() -> list[str]:
    """Register the pip-installed NVIDIA CUDA DLL directories. Idempotent."""
    global _DONE
    purelib = sysconfig.get_paths().get("purelib", "")
    bins = glob.glob(os.path.join(purelib, "nvidia", "*", "bin"))
    if _DONE or not bins:
        return bins
    for b in bins:
        try:
            os.add_dll_directory(b)
        except (OSError, AttributeError):
            pass
    os.environ["PATH"] = os.pathsep.join(bins) + os.pathsep + os.environ.get("PATH", "")
    logger.debug(f"CUDA DLL dirs registered for Whisper: {len(bins)} folder(s)")
    _DONE = True
    return bins


def cuda_available() -> bool:
    """True if ctranslate2 can actually see a CUDA GPU."""
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


# Run on import -- callers just `import cuda_dlls` before touching Whisper.
setup()
