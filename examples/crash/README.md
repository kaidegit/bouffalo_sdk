# crash_v1

## Support only linux

## Support CHIP

|      CHIP        | Remark |
|:----------------:|:------:|
|bl616/bl618       |        |

## Compile

- bl616/bl618
- comment CONFIG_COREDUMP_V2, default is v1

```
make CHIP=bl616 BOARD=bl616dk
```

## Flash

```
make flash CHIP=chip_name COMX=xxx # xxx is your com name
```

## coredump in flash usage

1. Enter crash in cli, wait log print. At the same time, a coredump will be generated in the core partition.

2. Get bin format crash binary, need to enter burning mode(CHIP_EN + RESET) first

```
(bl616dk)
../../tools/bflb_tools/bouffalo_flash_cube/BLFlashCommand-ubuntu --chip bl616 --read --start 0x2f8000 --len 0x80000 --file crash.bin --port /dev/ttyACM0
mv ../../tools/bflb_tools/bouffalo_flash_cube/crash.bin .

(bl616dk CONFIG_PSRAM = 1)
../../tools/bflb_tools/bouffalo_flash_cube/BLFlashCommand-ubuntu --chip bl616 --read --start 0x6e0000 --len 0x480000 --file crash.bin --port /dev/ttyACM0
mv ../../tools/bflb_tools/bouffalo_flash_cube/crash.bin .
```

3. Start bugkiller server

```
../../tools/crash_tools/v1/bugkiller_linux_amd64 -d crash.bin -e build/build_out/crash_bl616.elf -p 1234
```

4. Start debug

comment `restore build/build_out/crash_bl616.elf` and `restore crash.bin` in gdb.init file.
```
riscv64-unknown-elf-gdb -x gdb.init -se build/build_out/crash_bl616.elf
```

## coredump in log usage

1. Enter crash in cli, wait log print.

2. Save log to `1.log`.

3. If one log contains multiple coredumps, split them first with `tools/byai/crash_capture.py`:

```
../../tools/byai/crash_capture.py 1.log
[crash_capture] find 1 coredump
....
[crash_capture] find n coredump
```

This generates `tools/byai/output/coredump1` ... `tools/byai/output/coredumpn`.

4. Select the coredump you want to inspect, then start the local GDB RSP server directly:

```
../../tools/byai/coredump.py ../../tools/byai/output/coredump1 build/build_out/crash_bl616.elf
```

The script will:

- parse the selected log/coredump file
- load the last valid coredump into the virtual memory bus
- auto-select a free local port
- start `riscv64-unknown-elf-gdb`
- run `file`, `target remote`, `restore`
- source `tools/bouffalo_sdk.gdb` automatically

5. If you do not want auto-start, or want to connect manually, follow the commands printed by `coredump.py`. The manual order is:

```
(gdb) file build/build_out/crash_bl616.elf
(gdb) target remote localhost:<port>
(gdb) restore build/build_out/crash_bl616.elf
(gdb) source ../../tools/bouffalo_sdk.gdb
```

## note

- `tools/byai/crash_capture.py` writes split coredumps as plain UTF-8 text with Unix `\n` line endings.
- `tools/byai/coredump.py` reuses `tools/byai/crash_capture.py` parsing logic and always loads the last valid coredump from the selected file.
