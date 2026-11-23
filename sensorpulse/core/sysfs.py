"""Acceso a sysfs/procfs con raíz configurable (para pruebas con árboles falsos).

SENSORPULSE_SYSROOT=/ruta/captura hace que todo se lea desde esa raíz.
"""

import errno
import os

SYSROOT = os.environ.get("SENSORPULSE_SYSROOT", "/")


def p(*parts):
    """Ruta absoluta dentro de la raíz configurada."""
    return os.path.join(SYSROOT, *[x.lstrip("/") for x in parts])


def rel(path):
    """Ruta relativa a la raíz (para IDs estables)."""
    root = os.path.realpath(SYSROOT)
    real = os.path.realpath(path)
    # con separador: /tmp/sys no es prefijo de /tmp/sys2/...
    if root != "/" and (real == root or real.startswith(root + os.sep)):
        real = real[len(root):]
    return "/" + real.lstrip("/")


def read_text(path, default=None):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return default


def read_int(path, default=None):
    t = read_text(path)
    if t is None:
        return default
    try:
        return int(t.split()[0])
    except (ValueError, IndexError):
        return default


class FdCache:
    """Mantiene abiertos los ficheros sysfs y los relee con pread (barato)."""

    def __init__(self):
        self._fds = {}

    def read_int(self, path):
        fd = self._fds.get(path)
        try:
            if fd is None:
                fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
                self._fds[path] = fd
            data = os.pread(fd, 64, 0)
            return int(data.split()[0])
        except (OSError, ValueError, IndexError) as e:
            # ENODATA/EIO/EAGAIN: sensor sin dato ahora mismo (o dGPU dormida en kernel >= 6.13)
            if isinstance(e, OSError) and e.errno in (errno.ENOENT, errno.ENODEV, errno.EBADF):
                self.forget(path)
            return None

    def forget(self, path):
        fd = self._fds.pop(path, None)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def close(self):
        for path in list(self._fds):
            self.forget(path)
