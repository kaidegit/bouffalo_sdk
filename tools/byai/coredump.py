#!/usr/bin/env python3
"""
GDB Local Target Server

This script runs a standalone GDB RSP server for post-mortem debugging.
It parses the input log through crash_capture.py, loads the last valid
coredump into a sparse virtual memory bus, and starts GDB against the
provided ELF.

Usage:
    python tools/byai/coredump.py <log_file> <elf_file> [options]

    # Basic usage - auto-select a free port and start GDB
    python tools/byai/coredump.py demo.log firmware.elf

    # Use custom GDB init file
    python tools/byai/coredump.py demo.log firmware.elf -x gdb.init

The script will automatically:
1. Ask the OS for a free local port
2. Parse the log and load the last valid coredump into virtual memory
3. Start the GDB server
4. Start riscv64-unknown-elf-gdb with the ELF file
5. Run `file`, `target remote`, and `restore`
6. Source `tools/bouffalo_sdk.gdb` and any extra `-x` init file

If you need to connect manually from GDB:
    (gdb) file firmware.elf
    (gdb) target remote localhost:<port>
    (gdb) restore firmware.elf
    (gdb) source tools/bouffalo_sdk.gdb
    (gdb) x/10x 0x23000000
    (gdb) info registers
"""

import sys
import os
import socket
import struct
import threading
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple, List
from dataclasses import dataclass

from crash_capture import parse_coredump_sections


def find_free_port() -> int:
    """Ask the OS for an available local TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class MemoryChunk:
    """Backing storage for a memory block with realloc-like growth."""
    buffer: bytearray
    length: int = 0

    @classmethod
    def from_bytes(cls, data: bytes, reserve: Optional[int] = None) -> "MemoryChunk":
        capacity = max(len(data), reserve or 0)
        chunk = cls(bytearray(capacity), len(data))
        if data:
            chunk.buffer[:len(data)] = data
        return chunk

    def read(self, offset: int, size: int) -> bytes:
        return bytes(self.buffer[offset:offset + size])

    def write(self, offset: int, data: bytes) -> None:
        end = offset + len(data)
        if end > self.length:
            self.length = end
        self.buffer[offset:end] = data

    def append(self, data: bytes) -> None:
        old_length = self.length
        self.ensure_capacity(old_length + len(data))
        self.write(old_length, data)

    def ensure_capacity(self, required: int) -> None:
        if required <= len(self.buffer):
            return
        new_capacity = max(len(self.buffer) * 2, 64)
        while new_capacity < required:
            new_capacity *= 2
        self.buffer.extend(b"\x00" * (new_capacity - len(self.buffer)))


@dataclass
class MemoryBlock:
    """A contiguous memory block."""
    base: int
    length: int
    chunk: MemoryChunk

    def contains(self, addr: int, size: int) -> bool:
        """Check if this block contains the address range [addr, addr+size)."""
        return self.base <= addr and (self.base + self.length) >= (addr + size)

    @property
    def data(self) -> bytes:
        return self.chunk.read(0, self.length)


class MemoryBus:
    """
    Sparse memory block manager supporting up to 4GB address space.

    Features:
    - Dynamic address space creation
    - Automatic merging of adjacent/overlapping blocks
    - No fragmentation
    - Optional init mode: restrict writes to pre-initialized segments only
    """

    ADDR_MASK = 0xFFFFFFFF

    def __init__(self, init_only: bool = False):
        """
        Initialize MemoryBus.

        Args:
            init_only: If True, only allow stores within pre-initialized segments.
                       If False, allow dynamic creation of memory blocks (default).
        """
        self.blocks: List[MemoryBlock] = []
        self.init_only = init_only
        self._initialized = False
        self._last_append_idx: Optional[int] = None

    def init_blocks(self, blocks: List[tuple]):
        """
        Initialize memory bus with pre-defined memory blocks.

        Args:
            blocks: List of (address, data_bytes, name) tuples.
                    The data will be pre-loaded into memory blocks.

        Returns:
            Number of blocks initialized.
        """
        self.blocks = []
        for addr, data, name in blocks:
            # Create block with actual data pre-loaded
            chunk = MemoryChunk.from_bytes(data)
            self.blocks.append(MemoryBlock(addr, len(data), chunk))
        self._initialized = True
        return len(self.blocks)

    def add_block(self, block: MemoryBlock):
        """
        Add a single memory block to the bus.

        Args:
            block: MemoryBlock to add.
        """
        self.blocks.append(block)
        self._initialized = True
        self._last_append_idx = len(self.blocks) - 1

    def _find_block(self, addr: int, size: int) -> Optional[int]:
        """Find a block that contains the address range [addr, addr+size)."""
        for i, block in enumerate(self.blocks):
            if block.contains(addr, size):
                return i
        return None

    def _is_within_initialized(self, addr: int, size: int) -> bool:
        """Check if the address range is within any initialized segment."""
        end_addr = addr + size
        for block in self.blocks:
            block_end = block.base + block.length
            # Check for overlap
            if block_end >= addr and block.base <= end_addr:
                return True
        return False

    def load(self, addr: int, size: int) -> Optional[bytes]:
        """
        Load data from address range [addr, addr+size).

        Returns None if address range is not mapped.
        """
        addr &= self.ADDR_MASK
        idx = self._find_block(addr, size)
        if idx is not None:
            block = self.blocks[idx]
            offset = addr - block.base
            return block.chunk.read(offset, size)
        return None

    def store(self, addr: int, data: bytes) -> bool:
        """
        Store data to address range [addr, addr+len(data)).

        Match gdbstub.c behavior for sequential restore writes:
        - overwrite inside an existing block
        - append to the end of an existing block
        - create a new block if no existing block matches

        Returns False when block table is full, True otherwise.
        """
        addr &= self.ADDR_MASK
        size = len(data)

        if size == 0:
            return True

        if self._last_append_idx is not None:
            last_block = self.blocks[self._last_append_idx]
            if last_block.base + last_block.length == addr:
                last_block.chunk.append(data)
                last_block.length = last_block.chunk.length
                return True

        for i, block in enumerate(self.blocks):
            if block.contains(addr, size):
                block.chunk.write(addr - block.base, data)
                block.length = block.chunk.length
                self._last_append_idx = i
                return True

            if block.base + block.length == addr:
                block.chunk.append(data)
                block.length = block.chunk.length
                self._last_append_idx = i
                return True

        self.blocks.append(MemoryBlock(addr, size, MemoryChunk.from_bytes(data)))
        self._last_append_idx = len(self.blocks) - 1
        return True

def parse_coredump_blocks(log_file):
    """
    Parse coredump from log file and return memory blocks directly.

    Returns:
        List of (address, data_bytes, name) tuples, or None if parsing failed.
    """
    log_path = Path(log_file)
    if not log_path.exists():
        print(f"Error: Log file not found: {log_file}", file=sys.stderr)
        return None

    print(f"Parsing coredump from: {log_file}", file=sys.stderr)
    try:
        coredumps = parse_coredump_sections(log_path)
    except Exception as exc:
        print(f"Failed to parse coredump via crash_capture.py: {exc}", file=sys.stderr)
        return None

    if not coredumps:
        print("No COREDUMP sections found in log", file=sys.stderr)
        return None

    data_blocks = [(block.addr, block.data, block.name) for block in coredumps[-1]]
    if not data_blocks:
        print("No valid data blocks found in last coredump", file=sys.stderr)
        return None

    print(f"Found {len(coredumps)} COREDUMP sections, processing the last one", file=sys.stderr)
    print(f"Parsed {len(data_blocks)} memory blocks", file=sys.stderr)
    return data_blocks

# ==============================================================================
# RISC-V Register State
# ==============================================================================

class RiscvRegs:
    """RISC-V register state."""

    # RISCV_REG_X0_ZERO = 0, RISCV_REG_F31_FT11 = 64, RISCV_REG_MAX = 68
    REG_COUNT = 68
    # Match C code: send registers 0-64 inclusive (RISCV_REG_F31_FT11 + 1 = 65)
    REG_G_COUNT = 65

    def __init__(self):
        self.regs = [0] * self.REG_COUNT

    def get(self, idx: int) -> int:
        if 0 <= idx < len(self.regs):
            return self.regs[idx]
        return 0

    def set(self, idx: int, value: int):
        if 0 <= idx < len(self.regs):
            self.regs[idx] = value & 0xFFFFFFFF


# ==============================================================================
# GDB RSP Protocol Implementation
# ==============================================================================

class RspState:
    INIT = 0
    STARTED = 1
    CHECKSUM1 = 2
    CHECKSUM2 = 3


class GdbClientHandler:
    """GDB RSP client handler."""

    RSP_BUFF_LEN = 4096  # Larger buffer for SREC restore

    def __init__(self, conn: socket.socket, addr: Tuple[str, int], server_ref, log_file: Optional[str] = None):
        self.conn = conn
        self.addr = addr
        self.server = server_ref
        self.state = RspState.INIT
        self.no_ack_mode = False
        self.memory = MemoryBus()
        self.regs = RiscvRegs()
        self.running = True

        # Parse log file and init memory bus if provided
        if log_file:
            blocks = parse_coredump_blocks(log_file)
            if blocks:
                count = self.memory.init_blocks(blocks)
                print(f"Initialized {count} memory blocks from {log_file}")
            else:
                print(f"Warning: Failed to parse coredump from {log_file}")

    def send_ack(self, ack: bytes):
        if not self.no_ack_mode:
            try:
                self.conn.sendall(ack)
            except:
                pass

    def send_reply(self, data: bytes):
        checksum = sum(data) & 0xFF
        packet = b'$' + data + b'#' + f"{checksum:02X}".encode()
        try:
            self.conn.sendall(packet)
        except:
            pass

    def send_reply_hex(self, data: bytes) -> bool:
        try:
            hex_data = data.hex().upper().encode()
            self.send_reply(hex_data)
            return True
        except:
            self.send_reply(b"E06")
            return False

    def hex_to_int(self, s: str) -> int:
        return int(s, 16) if s else 0

    def hex_to_bytes(self, s: str) -> bytes:
        return bytes.fromhex(s)

    def handle_query(self, packet: str) -> bool:
        if packet[0:1] == "C":
            self.send_reply(f"QC0".encode())
        elif packet[:3] == "wr.":
            self.send_reply(b"")
        elif "Attached" in packet and packet.find("Attached") == 0:
            self.send_reply(b"1")
        elif "Offsets" in packet and packet.find("Offsets") == 0:
            self.send_reply(b"Text=0;Data=0;Bss=0")
        elif "Supported" in packet and packet.find("Supported") == 0:
            # Match gdbstub.c: do NOT enable qXfer:features:read (wrapped in #if 0)
            self.send_reply(
                f"PacketSize={self.RSP_BUFF_LEN:X};QStartNoAckMode+;type=noneocd".encode()
            )
        else:
            self.send_reply(b"")
        return True

    def handle_read_memory(self, packet: str) -> bool:
        try:
            parts = packet.split(",")
            addr = self.hex_to_int(parts[0])
            length = self.hex_to_int(parts[1].split("#")[0])

            data = self.memory.load(addr, length)
            if data is None:
                self.send_reply(b"E0A")
                return False

            return self.send_reply_hex(data)
        except:
            self.send_reply(b"E06")
            return False

    def handle_write_memory(self, packet: str) -> bool:
        try:
            parts = packet.split(",")
            addr = self.hex_to_int(parts[0])
            length_parts = parts[1].split(":")
            length = self.hex_to_int(length_parts[0])

            if length == 0:
                self.send_reply(b"OK")
                return True

            data_hex = length_parts[1].split("#")[0]
            data = self.hex_to_bytes(data_hex)

            ret = self.memory.store(addr, data)
            if ret:
                self.send_reply(b"OK")
                return True
            else:
                self.send_reply(b"E0A")
                return False
        except:
            self.send_reply(b"E06")
            return False

    def handle_write_memory_binary(self, packet: str) -> bool:
        """Handle 'X' write memory (binary, used by restore command)."""
        try:
            parts = packet.split(",")
            addr = self.hex_to_int(parts[0])
            length_parts = parts[1].split(":")
            length = self.hex_to_int(length_parts[0])

            if length == 0:
                self.send_reply(b"OK")
                return True

            # Extract binary data with escape handling
            data_str = length_parts[1].split("#")[0]
            data = bytearray()
            i = 0
            while i < len(data_str):
                if i + 1 < len(data_str) and data_str[i] == '}':
                    # Escaped character
                    i += 1
                    data.append(ord(data_str[i]) ^ 0x20)
                else:
                    data.append(ord(data_str[i]))
                i += 1

            ret = self.memory.store(addr, bytes(data))
            if ret:
                self.send_reply(b"OK")
                return True
            else:
                self.send_reply(b"E0A")
                return False
        except:
            self.send_reply(b"E06")
            return False

    def handle_get_regs(self) -> bool:
        try:
            # Send 65 registers matching C code: x0-x31 (32), pc (1), f0-f31 (32)
            # Use little-endian byte order to match C code's raw memory transmission
            reg_data = b""
            for i in range(RiscvRegs.REG_G_COUNT):  # 65 registers
                reg_data += struct.pack("<I", self.regs.get(i))
            return self.send_reply_hex(reg_data)
        except:
            return False

    def handle_set_regs(self, packet: str) -> bool:
        try:
            data = self.hex_to_bytes(packet)
            for i in range(min(len(data) // 4, RiscvRegs.REG_G_COUNT)):
                val = struct.unpack_from("<I", data, i * 4)[0]
                self.regs.set(i, val)
            self.send_reply(b"OK")
            return True
        except:
            self.send_reply(b"E0A")
            return False

    def handle_get_reg(self, packet: str) -> bool:
        try:
            reg_idx = self.hex_to_int(packet)
            if reg_idx > RiscvRegs.REG_COUNT:  # RISCV_REG_MAX
                self.send_reply(b"E0A")
                return False
            val = self.regs.get(reg_idx)
            reg_data = struct.pack("<I", val)
            return self.send_reply_hex(reg_data)
        except:
            self.send_reply(b"E0A")
            return False

    def handle_set_reg(self, packet: str) -> bool:
        try:
            equal_idx = packet.find("=")
            reg_idx = self.hex_to_int(packet[:equal_idx])
            if reg_idx > RiscvRegs.REG_COUNT:  # RISCV_REG_MAX
                self.send_reply(b"E0A")
                return False
            # Skip '=', get value
            val_hex = packet[equal_idx + 1:].split("#")[0]
            # Convert hex string to bytes, then unpack as little-endian uint32
            # This matches the byte order encoding in GDB RSP protocol
            val_bytes = self.hex_to_bytes(val_hex)
            if len(val_bytes) < 4:
                # Pad with zeros if less than 4 bytes
                val_bytes += b'\x00' * (4 - len(val_bytes))
            val = struct.unpack("<I", val_bytes)[0]
            self.regs.set(reg_idx, val)
            self.send_reply(b"OK")
            return True
        except:
            self.send_reply(b"E0A")
            return False

    def parse_packet(self, packet: str):
        if not packet:
            return

        cmd = packet[0]
        rest = packet[1:]

        if cmd == '?':
            self.send_reply(b"S02")  # SIGINT
        elif cmd == 'D':
            self.send_reply(b"OK")
        elif cmd == 'Q':
            if "StartNoAckMode" in rest and rest.find("StartNoAckMode") == 0:
                self.send_reply(b"OK")
                self.no_ack_mode = True
            else:
                self.send_reply(b"")
        elif cmd == 'q':
            self.handle_query(rest)
        elif cmd == 'g':
            self.handle_get_regs()
        elif cmd == 'G':
            self.handle_set_regs(rest)
        elif cmd == 'p':
            self.handle_get_reg(rest)
        elif cmd == 'P':
            self.handle_set_reg(rest)
        elif cmd == 'm':
            self.handle_read_memory(rest)
        elif cmd == 'M':
            self.handle_write_memory(rest)
        elif cmd == 'X':
            self.handle_write_memory_binary(rest)
        elif cmd == 'H':
            self.send_reply(b"OK")
        elif cmd == 'v':
            if "Cont?" in rest and rest.find("Cont?") == 0:
                self.send_reply(b"vCont;c;s;C;S")
            elif "Cont;" in rest and rest.find("Cont;") == 0:
                # Don't support vCont, just break without reply
                pass
            elif "MustReplyEmpty" in rest and rest.find("MustReplyEmpty") == 0:
                self.send_reply(b"")
            else:
                self.send_reply(b"")
        elif cmd == 'z':
            # Don't support breakpoint, just print (no reply)
            pass
        elif cmd == 'Z':
            # Don't support breakpoint, just print (no reply)
            pass
        else:
            self.send_reply(b"")

    def recv_packet(self) -> Optional[bytearray]:
        buffer = bytearray()
        state = RspState.INIT
        checksum_calc = 0
        checksum_recv = 0
        checksum_hex = ""

        self.conn.settimeout(0.5)

        while self.running:
            try:
                data = self.conn.recv(1)
                if not data:
                    return None

                byte = data[0]

                if state == RspState.INIT:
                    if byte == ord('$'):
                        state = RspState.STARTED
                        checksum_calc = 0
                        buffer = bytearray()

                elif state == RspState.STARTED:
                    if byte == ord('#'):
                        state = RspState.CHECKSUM1
                    else:
                        if len(buffer) < self.RSP_BUFF_LEN:
                            buffer.append(byte)
                            checksum_calc = (checksum_calc + byte) & 0xFF

                elif state == RspState.CHECKSUM1:
                    checksum_hex = chr(byte)
                    state = RspState.CHECKSUM2

                elif state == RspState.CHECKSUM2:
                    checksum_hex += chr(byte)
                    checksum_recv = int(checksum_hex, 16)

                    if not self.no_ack_mode:
                        if checksum_recv != checksum_calc:
                            self.send_ack(b"-")
                            state = RspState.INIT
                            continue
                        self.send_ack(b"+")

                    return buffer

            except socket.timeout:
                continue
            except:
                return None

        return None

    def run(self):
        print(f"Target local: Client connected from {self.addr[0]}:{self.addr[1]}")

        try:
            while self.running:
                packet = self.recv_packet()
                if packet is None:
                    break

                packet_str = packet.decode('latin-1', errors='ignore')
                self.parse_packet(packet_str)

        except Exception as e:
            print(f"Target local: Client error: {e}")
        finally:
            print(f"Target local: Client disconnected")
            self.conn.close()
            self.server.notify_client_done()


class LocalTargetServer:
    """Local GDB RSP server."""

    def __init__(self, port: int, log_file: Optional[str] = None):
        self.port = port
        self.log_file = log_file
        self.sock = None
        self.thread = None
        self.running = False
        self.clients = []
        self.lock = threading.Lock()

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", self.port))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(1)
        self.running = True

        def server_loop():
            while self.running:
                try:
                    self.sock.settimeout(1.0)
                    conn, addr = self.sock.accept()
                    client = GdbClientHandler(conn, addr, self, self.log_file)
                    with self.lock:
                        self.clients.append(client)
                    thread = threading.Thread(target=client.run)
                    thread.daemon = True
                    thread.start()
                except socket.timeout:
                    continue
                except:
                    break

        self.thread = threading.Thread(target=server_loop)
        self.thread.daemon = True
        self.thread.start()

    def notify_client_done(self):
        with self.lock:
            self.clients = [c for c in self.clients if c.running]

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()
        with self.lock:
            for client in self.clients:
                client.running = False


# ==============================================================================
# Main Entry Point
# ==============================================================================

def print_usage():
    """Print usage information."""
    print(__doc__)


def main():
    """Main entry point for standalone GDB server."""
    import argparse

    parser = argparse.ArgumentParser(
        description='GDB Local Target Server - Standalone GDB RSP server for post-mortem analysis',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        'log_file',
        help='Path to coredump log file to load'
    )

    parser.add_argument(
        'elf_file',
        help='Path to ELF file for symbol loading'
    )

    parser.add_argument(
        '-x', '--gdb-init',
        dest='gdb_init',
        default=None,
        help='Path to GDB init file (e.g., gdb.init)'
    )

    args = parser.parse_args()

    # Ask OS for a free local port
    port = find_free_port()
    print(f"Auto-selected free port: {port}")

    log_file = args.log_file
    elf_file = args.elf_file
    gdb_init = args.gdb_init
    default_gdb_init = Path(__file__).resolve().parents[1] / 'bouffalo_sdk.gdb'

    # Create and start server
    print(f"Starting local GDB server on port {port}...")
    server = LocalTargetServer(port, log_file)
    server.start()

    print(f"GDB server ready. Connect from GDB using:")
    print(f"  (gdb) file {elf_file}")
    print(f"  (gdb) target remote localhost:{port}")
    print(f"  (gdb) restore {elf_file}")
    if default_gdb_init.exists():
        print(f"  (gdb) source {default_gdb_init}")

    # Auto-start GDB client
    # Give server a moment to be ready
    time.sleep(0.5)

    # Build GDB command with -ex parameters
    gdb_cmd = ['riscv64-unknown-elf-gdb']
    gdb_cmd.extend(['-ex', f'file {elf_file}'])
    gdb_cmd.extend(['-ex', f'target remote localhost:{port}'])
    gdb_cmd.extend(['-ex', f'restore {elf_file}'])
    if default_gdb_init.exists():
        gdb_cmd.extend(['-x', str(default_gdb_init)])
    else:
        print(f"Warning: default GDB init not found: {default_gdb_init}")
    if gdb_init:
        gdb_cmd.extend(['-x', gdb_init])

    print(f"Auto-starting GDB client: {' '.join(gdb_cmd)}")

    try:
        gdb_process = subprocess.Popen(gdb_cmd)
    except FileNotFoundError:
        print(f"Warning: {args.gdb} not found. Please start GDB manually.")
        gdb_process = None
    except Exception as e:
        print(f"Warning: Failed to start GDB: {e}")
        gdb_process = None

    try:
        # Keep server running
        while server.running:
            # Check if GDB process is still running
            if gdb_process is not None:
                ret = gdb_process.poll()
                if ret is not None:
                    # GDB has exited
                    print(f"GDB client exited (code={ret})")
                    gdb_process = None
                    # Shutdown server when GDB exits
                    break

        # Normal exit after GDB disconnects
        if server.running:
            print("\nShutting down server...")
            server.stop()
            print("Server stopped.")

    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.stop()

        # Wait for GDB process to exit
        if gdb_process is not None:
            ret = gdb_process.poll()
            if ret is None:
                print("Waiting for GDB client to exit...")
                gdb_process.wait()
            print("GDB client stopped.")

        print("Server stopped.")


if __name__ == '__main__':
    main()
