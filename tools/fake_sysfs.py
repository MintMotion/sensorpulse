#!/usr/bin/env python3
"""Genera árboles sysfs/procfs falsos para pruebas y demostraciones.

Uso: fake_sysfs.py DESTINO [desktop|laptop]
Después: SENSORPULSE_SYSROOT=DESTINO sensorpulse ...
"""

import os
import sys


def w(root, path, text):
    full = os.path.join(root, path.lstrip("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(str(text) + "\n")


def hwmon(root, idx, devrel, name, files):
    """Crea /sys/devices/<devrel>/hwmon/hwmonN y el enlace en /sys/class/hwmon."""
    devdir = os.path.join(root, "sys/devices", devrel)
    hwdir = os.path.join(devdir, "hwmon", "hwmon%d" % idx)
    os.makedirs(hwdir, exist_ok=True)
    w(root, os.path.join("sys/devices", devrel, "hwmon", "hwmon%d" % idx, "name"), name)
    for k, v in files.items():
        w(root, os.path.join("sys/devices", devrel, "hwmon", "hwmon%d" % idx, k), v)
    link = os.path.join(hwdir, "device")
    if not os.path.lexists(link):
        os.symlink(devdir, link)
    cls = os.path.join(root, "sys/class/hwmon")
    os.makedirs(cls, exist_ok=True)
    l2 = os.path.join(cls, "hwmon%d" % idx)
    if not os.path.lexists(l2):
        os.symlink(hwdir, l2)
    return devdir


def common(root, cores=8):
    w(root, "proc/cpuinfo", "processor\t: 0\nmodel name\t: AMD Ryzen 7 7700X 8-Core Processor\n")
    w(root, "proc/stat", "cpu  10000 0 5000 80000 500 0 100 0 0 0\n")
    w(root, "proc/meminfo", "MemTotal:       32768000 kB\nMemFree:  8000000 kB\nMemAvailable:   20000000 kB\n")
    for i in range(cores):
        base = "sys/devices/system/cpu/cpu%d/cpufreq/" % i
        w(root, base + "scaling_cur_freq", 3800000 + i * 50000)
        w(root, base + "cpuinfo_max_freq", 5400000)


def desktop(root):
    common(root)
    hwmon(root, 0, "pci0000:00/0000:00:18.3", "k10temp",
          {"temp1_input": 54250, "temp1_label": "Tctl",
           "temp3_input": 51000, "temp3_label": "Tccd1"})
    dev = hwmon(root, 1, "pci0000:00/0000:00:01.1/0000:03:00.0", "amdgpu",
                {"temp1_input": 47000, "temp1_label": "edge", "temp1_crit": 100000,
                 "temp2_input": 58000, "temp2_label": "junction", "temp2_crit": 110000,
                 "temp3_input": 62000, "temp3_label": "mem", "temp3_crit": 105000,
                 "fan1_input": 1450, "power1_average": 38000000, "power1_cap": 263000000,
                 "in0_input": 850, "in0_label": "vddgfx"})
    w(root, os.path.relpath(os.path.join(dev, "gpu_busy_percent"), root), 12)
    w(root, os.path.relpath(os.path.join(dev, "vendor"), root), "0x1002")
    w(root, os.path.relpath(os.path.join(dev, "power/runtime_status"), root), "active")
    nv = hwmon(root, 2, "pci0000:00/0000:00:02.4/0000:05:00.0/nvme/nvme0", "nvme",
               {"temp1_input": 41850, "temp1_label": "Composite", "temp1_max": 84850,
                "temp1_crit": 89850, "temp2_input": 44850, "temp2_label": "Sensor 1"})
    w(root, os.path.relpath(os.path.join(nv, "model"), root), "WD_BLACK SN850X 1000GB")
    hd = hwmon(root, 3, "pci0000:00/0000:00:08.1/ata2/host1/target1:0:0/1:0:0:0", "drivetemp",
               {"temp1_input": 36000, "temp1_crit": 60000, "temp1_max": 55000})
    w(root, os.path.relpath(os.path.join(hd, "model"), root), "ST4000DM004-2CV1")
    w(root, os.path.relpath(os.path.join(hd, "vendor"), root), "ATA")
    w(root, os.path.relpath(os.path.join(hd, "block/sda/queue/rotational"), root), 1)
    w(root, os.path.relpath(os.path.join(hd, "block/sda/removable"), root), 0)
    # SSD SATA (drivetemp sin partes móviles): se puede leer a menudo
    sd = hwmon(root, 6, "pci0000:00/0000:00:08.1/ata3/host2/target2:0:0/2:0:0:0", "drivetemp",
               {"temp1_input": 33000, "temp1_crit": 70000, "temp1_max": 65000})
    w(root, os.path.relpath(os.path.join(sd, "model"), root), "Samsung SSD 870 EVO 1TB")
    w(root, os.path.relpath(os.path.join(sd, "vendor"), root), "ATA")
    w(root, os.path.relpath(os.path.join(sd, "block/sdb/queue/rotational"), root), 0)
    w(root, os.path.relpath(os.path.join(sd, "block/sdb/removable"), root), 0)
    hwmon(root, 4, "platform/nct6775.656", "nct6798",
          {"temp1_input": 34000, "temp1_label": "SYSTIN", "temp2_input": 41000,
           "temp2_label": "CPUTIN", "fan1_input": 820, "fan2_input": 1120, "fan3_input": 0,
           "in0_input": 1048, "in0_label": "Vcore"})
    hwmon(root, 5, "platform/spd5118.0", "spd5118", {"temp1_input": 39500})


def laptop(root):
    common(root, cores=4)
    w(root, "proc/cpuinfo", "processor\t: 0\nmodel name\t: Intel(R) Core(TM) i7-1165G7 @ 2.80GHz\n")
    hwmon(root, 0, "platform/coretemp.0", "coretemp",
          {"temp1_input": 61000, "temp1_label": "Package id 0", "temp1_max": 100000,
           "temp1_crit": 100000, "temp2_input": 58000, "temp2_label": "Core 0",
           "temp3_input": 60000, "temp3_label": "Core 1"})
    for i in range(4):
        w(root, "sys/devices/system/cpu/cpu%d/thermal_throttle/core_throttle_count" % i, 3)
        w(root, "sys/devices/system/cpu/cpu%d/thermal_throttle/package_throttle_count" % i, 7)
    hwmon(root, 1, "platform/thinkpad_hwmon", "thinkpad",
          {"fan1_input": 2650, "temp1_input": 55000})
    hwmon(root, 2, "virtual/thermal/thermal_zone0", "acpitz", {"temp1_input": 50000})
    # dGPU NVIDIA dormida: no debe leerse
    w(root, "sys/bus/pci/devices/0000:01:00.0/vendor", "0x10de")
    w(root, "sys/bus/pci/devices/0000:01:00.0/class", "0x030200")
    w(root, "sys/bus/pci/devices/0000:01:00.0/power/runtime_status", "suspended")
    os.symlink("../../../../bus/pci/drivers/nvidia",
               os.path.join(root, "sys/bus/pci/devices/0000:01:00.0/driver"))
    nv = hwmon(root, 3, "pci0000:00/0000:00:06.0/0000:02:00.0/nvme/nvme0", "nvme",
               {"temp1_input": 38850, "temp1_label": "Composite", "temp1_max": 81850,
                "temp1_crit": 84850})
    w(root, os.path.relpath(os.path.join(nv, "model"), root), "SAMSUNG MZVLB512HBJQ")
    b = "sys/class/power_supply/BAT0/"
    for k, v in {"type": "Battery", "capacity": 86, "status": "Discharging",
                 "power_now": 9300000, "energy_full": 46000000,
                 "energy_full_design": 50000000, "cycle_count": 212,
                 "manufacturer": "SMP", "model_name": "5B10W13930"}.items():
        w(root, b + k, v)


def tr1950x(root):
    """Threadripper 1950X en reposo: Tctl lleva +27 °C de desfase; Tdie es la real."""
    common(root, cores=16)
    w(root, "proc/cpuinfo", "processor\t: 0\nmodel name\t: AMD Ryzen Threadripper 1950X 16-Core Processor\n")
    w(root, "proc/stat", "cpu  1000 0 500 98000 50 0 10 0 0 0\n")
    hwmon(root, 0, "pci0000:00/0000:00:18.3", "k10temp",
          {"temp1_input": 67000, "temp1_label": "Tctl",
           "temp2_input": 40000, "temp2_label": "Tdie"})


if __name__ == "__main__":
    dest = sys.argv[1]
    kind = sys.argv[2] if len(sys.argv) > 2 else "desktop"
    {"desktop": desktop, "laptop": laptop, "tr1950x": tr1950x}[kind](dest)
    print("sysfs falso creado en", dest, "(", kind, ")")
