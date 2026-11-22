#!/usr/bin/env python3
"""Generate tracked sample files in server-data/ for every RFC 959 TYPE/STRU/MODE permutation.

Permutations:
  TYPE: A (ASCII), I (Image/Binary), E (EBCDIC)
  STRU: F (File),  R (Record),       P (Page)
  MODE: S (Stream), B (Block),       C (Compressed)

Total = 3 x 3 x 3 = 27 permutations.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import struct
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SERVER_DATA = BASE_DIR / "server-data"
SAMPLES_DIR = SERVER_DATA / "samples"


def make_mode_b_block(data: bytes, *, eor: bool = False, eof: bool = False) -> bytes:
    """Format bytes into an RFC 959 Mode B transmission block.

    What it does:
        Encapsulates payload bytes into an RFC 959 Section 3.4.2 Block Mode frame
        with a 1-byte descriptor header and 2-byte big-endian byte count.

    How it does:
        1. Encodes descriptor bit 7 (0x80) for EOF and bit 6 (0x40) for EOR.
        2. Validates that byte count does not exceed 65,535 bytes.
        3. Packs descriptor and count using struct.pack('>BH', descriptor, count).
        4. Concatenates header bytes with raw payload data and returns the block.
    """
    descriptor = 0
    if eof:
        descriptor |= 0x80
    if eor:
        descriptor |= 0x40
    count = len(data)
    if count > 65535:
        raise ValueError("Block length exceeds 65535 bytes")
    return struct.pack(">BH", descriptor, count) + data


def make_mode_c_compressed(data: bytes) -> bytes:
    """Encode bytes using the RFC 959 Section 3.4.3 Compressed Mode run-length scheme.

    What it does:
        Compresses a sequence of bytes using the standard 2-bit descriptor run-length encoding.

    How it does:
        1. Scans incoming bytes for repeated byte runs (>= 3 identical bytes, up to 64).
        2. Encodes space runs (0x20 in ASCII or 0x40 in EBCDIC) as 11xxxxxx (0xC0 | count - 1).
        3. Encodes non-space byte runs as 10xxxxxx (0x80 | count - 1) followed by the replicated byte.
        4. Encodes literal non-repeating byte chunks as 00xxxxxx (0x00 | count - 1) followed by raw bytes.
        5. Returns the resulting compressed bytearray as immutable bytes.
    """
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        # Check for run of same byte
        run = 1
        while i + run < n and data[i + run] == b and run < 64:
            run += 1
        if run >= 3:
            if b in (0x20, 0x40):  # space in ASCII (0x20) or EBCDIC (0x40)
                out.append(0xC0 | (run - 1))
            else:
                out.append(0x80 | (run - 1))
                out.append(b)
            i += run
        else:
            # Literal chunk
            chunk_len = min(64, n - i)
            # Make sure we don't start literal before a run >= 3
            j = 0
            while j < chunk_len:
                if j + 3 <= chunk_len and data[i + j] == data[i + j + 1] == data[i + j + 2]:
                    break
                j += 1
            if j == 0:
                j = 1
            out.append(0x00 | (j - 1))
            out.extend(data[i : i + j])
            i += j
    return bytes(out)


def make_page_payload(pages: list[tuple[int, bytes]]) -> bytes:
    """Encode indexed pages per RFC 959 Section 3.1.2.3 Page Structure specification.

    What it does:
        Constructs a structured page payload with headers and indexed byte slices.

    How it does:
        1. Iterates over indexed page tuples (page_num, page_bytes).
        2. Formats a 5-byte page header: Page Type (1 byte, 0=data, 1=eof), Page Number
           (2 bytes, big-endian), and Page Length (2 bytes, big-endian).
        3. Concatenates the packed header and page bytes into a contiguous byte stream.
    """
    payload = bytearray()
    for idx, (page_num, page_bytes) in enumerate(pages):
        is_last = (idx == len(pages) - 1)
        p_type = 1 if is_last else 0
        p_len = len(page_bytes)
        payload.extend(struct.pack(">BHH", p_type, page_num, p_len))
        payload.extend(page_bytes)
    return bytes(payload)


def generate_all():
    """Generate the complete 27-permutation RFC 959 demonstration suite and manifest.

    What it does:
        Creates welcome.txt and 27 authentic sample files in server-data/samples/ covering
        every combination of TYPE (A, I, E), STRU (F, R, P), and MODE (S, B, C), along with
        manifest.json and README.md.

    How it does:
        1. Prepares base payloads for ASCII text, Binary image, and EBCDIC mainframe cards.
        2. Iterates through all 27 combinations, formatting each according to TYPE, STRU, and MODE.
        3. Writes each sample to server-data/samples/ and calculates its SHA-256 digest.
        4. Writes server-data/welcome.txt in sandbox root explaining navigation.
        5. Writes server-data/samples/manifest.json and server-data/samples/README.md.
    """
    os.makedirs(SAMPLES_DIR, exist_ok=True)

    # 1. Sandbox root welcome file (TYPE A, STRU F, MODE S)
    welcome_text = (
        "========================================================================\n"
        "Welcome to the RFC 959 FTP Lab Sandbox Environment\n"
        "========================================================================\n\n"
        "This FTP server root directory contains interactive and automated test data.\n"
        "A complete suite of 27 tracked demonstration files covering EVERY permutation\n"
        "of RFC 959 parameter specifications is located in the '/samples' sub-directory:\n\n"
        "  - Representation Types (TYPE): ASCII (A), Image/Binary (I), EBCDIC (E)\n"
        "  - Data Structures    (STRU): File (F), Record (R), Page (P)\n"
        "  - Transmission Modes (MODE): Stream (S), Block (B), Compressed (C)\n\n"
        "To explore the permutations, use the client commands:\n"
        "  ftp> cd samples\n"
        "  ftp> ls\n"
        "  ftp> type A\n"
        "  ftp> stru F\n"
        "  ftp> mode S\n"
        "  ftp> stat\n"
        "  ftp> get type-A_stru-F_mode-S_ascii-file-stream.txt\n\n"
        "Consult /samples/README.md or Group_Project/FTP/README.md for full guidance.\n"
    )
    welcome_path = SERVER_DATA / "welcome.txt"
    welcome_path.write_bytes(welcome_text.encode("ascii"))
    print(f"Generated {welcome_path} ({welcome_path.stat().st_size} bytes)")

    # Define base representations
    # ASCII text
    ascii_text = (
        "RFC 959 File Transfer Protocol (FTP) - ASCII Stream Demonstration\n"
        "----------------------------------------------------------------\n"
        "TYPE: A (ASCII non-print representation with CRLF line terminators)\n"
        "STRU: F (Continuous file structure with no internal record delimiters)\n"
        "MODE: S (Stream mode terminated by closing the TCP data connection)\n\n"
        "Network-Lab 3.1 Group Project Demonstration File.\n"
        "Testing character conversion and transparent streaming over loopback TCP.\n"
    ).encode("ascii")

    # ASCII records (CSV tabular dataset)
    ascii_records = [
        "rec_id,module,protocol,rfc,status,description\r\n".encode("ascii"),
        "101,control,FTP,RFC 959,PASS,Command-Reply Control Stream\r\n".encode("ascii"),
        "102,transfer,Passive,RFC 959,PASS,Passive Data Socket Listening\r\n".encode("ascii"),
        "103,transfer,Active,RFC 959,PASS,Active Data Endpoint Initiation\r\n".encode("ascii"),
        "104,extension,EPSV/EPRT,RFC 2428,PASS,IPv4 Extended Passive/Active\r\n".encode("ascii"),
        "105,integrity,SHA-256,FIPS 180-4,PASS,End-to-End Cryptographic Match\r\n".encode("ascii"),
    ]
    ascii_rec_combined = b"".join(ascii_records)

    # ASCII pages
    ascii_pages = [
        (1, "--- RFC 959 Page 1: Architecture and Architectural Model ---\nControl Connection on TCP 2121; Data Connection dynamic.\n".encode("ascii")),
        (2, "--- RFC 959 Page 2: State Machine and Two-Phase Commit ---\nAtomic temporary staging file replaced upon verified data EOF.\n".encode("ascii")),
        (3, "--- RFC 959 Page 3: Concurrency and Thread Safety ---\nUp to 32 concurrent isolated sessions with sandbox path safety.\n".encode("ascii")),
    ]
    ascii_paged_data = make_page_payload(ascii_pages)

    # Binary raw payload
    bin_header = b"\x7fFTP_BINARY\x01\x00\x00\x00"
    bin_numbers = struct.pack(">4I4f", 1000, 2000, 3000, 4000, 3.14159, 2.71828, 1.41421, 0.57721)
    bin_bytes = bytes(range(256))
    bin_data = bin_header + bin_numbers + bin_bytes

    # Binary records (fixed 32-byte records)
    bin_records = []
    for i in range(8):
        rec = struct.pack(">IIff16s", i + 1, 1700000000 + i * 3600, 24.5 + i, 101.3 - i * 0.2, f"SENSOR_CH_{i:02d}".encode("ascii"))
        bin_records.append(rec)
    bin_rec_combined = b"".join(bin_records)

    # Binary pages
    bin_pages = [
        (10, struct.pack(">8I", 10, 20, 30, 40, 50, 60, 70, 80) + bytes(range(64))),
        (20, struct.pack(">8I", 110, 120, 130, 140, 150, 160, 170, 180) + bytes(range(64, 128))),
        (30, struct.pack(">8I", 210, 220, 230, 240, 250, 260, 270, 280) + bytes(range(128, 192))),
    ]
    bin_paged_data = make_page_payload(bin_pages)

    # EBCDIC text (IBM CP037)
    ebcdic_text_str = (
        "RFC 959 FTP PROTOCOL - EBCDIC MAINFRAME DEMONSTRATION FILE\n"
        "ENCODING: IBM CP037 EBCDIC CODEPAGE\n"
        "THIS FILE CONTAINS EBCDIC CHARACTERS FOR VENDOR SYSTEM TESTING\n"
    )
    ebcdic_text = ebcdic_text_str.encode("cp037")

    # EBCDIC 80-column card records
    card_lines = [
        "//JOB001   JOB (ACCT),'FTP DEMO',CLASS=A,MSGCLASS=X                       ",
        "//STEP1    EXEC PGM=NETWORK_LAB,PARM='RFC959'                             ",
        "//SYSIN    DD *                                                           ",
        "RECORD 0001: EBCDIC CARD IMAGE INPUT ROW ONE                              ",
        "RECORD 0002: EBCDIC CARD IMAGE INPUT ROW TWO                              ",
        "/*                                                                        ",
    ]
    ebcdic_records = [line.encode("cp037") for line in card_lines]
    ebcdic_rec_combined = b"".join(ebcdic_records)

    # EBCDIC pages
    ebcdic_pages = [
        (1, "EBCDIC REPORT PAGE 001 - HEADER AND JOB INITIALIZATION".encode("cp037")),
        (2, "EBCDIC REPORT PAGE 002 - DATA EXECUTION AND SOCKET TRANSMISSION".encode("cp037")),
        (3, "EBCDIC REPORT PAGE 003 - SUMMARY STATS AND SUCCESSFUL RETURN CODE".encode("cp037")),
    ]
    ebcdic_paged_data = make_page_payload(ebcdic_pages)

    manifest = []

    # Permutations mapping
    permutations = [
        # --- TYPE A (ASCII) ---
        (
            "A", "F", "S",
            "type-A_stru-F_mode-S_ascii-file-stream.txt",
            ascii_text,
            "Standard ASCII text stream with continuous file structure and CRLF delimiters.",
        ),
        (
            "A", "F", "B",
            "type-A_stru-F_mode-B_ascii-file-block.dat",
            make_mode_b_block(ascii_text[: len(ascii_text) // 2], eor=False)
            + make_mode_b_block(ascii_text[len(ascii_text) // 2 :], eof=True),
            "ASCII text framed into RFC 959 Mode B transmission blocks with byte-count headers.",
        ),
        (
            "A", "F", "C",
            "type-A_stru-F_mode-C_ascii-file-compressed.dat",
            make_mode_c_compressed(ascii_text),
            "ASCII text compressed using RFC 959 Section 3.4.3 run-length compression byte counts.",
        ),
        (
            "A", "R", "S",
            "type-A_stru-R_mode-S_ascii-record-stream.csv",
            ascii_rec_combined,
            "ASCII record stream (CSV rows) where each line represents an RFC 959 sequential record.",
        ),
        (
            "A", "R", "B",
            "type-A_stru-R_mode-B_ascii-record-block.dat",
            b"".join(
                make_mode_b_block(rec, eor=True, eof=(i == len(ascii_records) - 1))
                for i, rec in enumerate(ascii_records)
            ),
            "ASCII records packed into Mode B blocks with EOR descriptor bit set per record.",
        ),
        (
            "A", "R", "C",
            "type-A_stru-R_mode-C_ascii-record-compressed.dat",
            make_mode_c_compressed(ascii_rec_combined),
            "Sequential ASCII records encoded with Mode C run-length space/byte compression.",
        ),
        (
            "A", "P", "S",
            "type-A_stru-P_mode-S_ascii-page-stream.txt",
            ascii_paged_data,
            "Indexed ASCII page structure with RFC 959 page headers (page type, number, length).",
        ),
        (
            "A", "P", "B",
            "type-A_stru-P_mode-B_ascii-page-block.dat",
            make_mode_b_block(ascii_paged_data, eof=True),
            "Paged ASCII data encapsulated inside RFC 959 Mode B transmission blocks.",
        ),
        (
            "A", "P", "C",
            "type-A_stru-P_mode-C_ascii-page-compressed.dat",
            make_mode_c_compressed(ascii_paged_data),
            "Paged ASCII data with Mode C compressed page payloads.",
        ),

        # --- TYPE I (Binary / Image) ---
        (
            "I", "F", "S",
            "type-I_stru-F_mode-S_binary-file-stream.bin",
            bin_data,
            "Byte-exact raw binary data containing all 256 byte values and binary structs.",
        ),
        (
            "I", "F", "B",
            "type-I_stru-F_mode-B_binary-file-block.dat",
            make_mode_b_block(bin_data[: len(bin_data) // 2])
            + make_mode_b_block(bin_data[len(bin_data) // 2 :], eof=True),
            "Binary payload framed into RFC 959 Mode B transmission blocks with block headers.",
        ),
        (
            "I", "F", "C",
            "type-I_stru-F_mode-C_binary-file-compressed.gz",
            gzip.compress(bin_data, compresslevel=6),
            "Genuine gzip compressed binary stream conforming to RFC 959 compressed binary mode.",
        ),
        (
            "I", "R", "S",
            "type-I_stru-R_mode-S_binary-record-stream.dat",
            bin_rec_combined,
            "Binary record stream containing fixed 32-byte C-struct sensor records.",
        ),
        (
            "I", "R", "B",
            "type-I_stru-R_mode-B_binary-record-block.dat",
            b"".join(
                make_mode_b_block(rec, eor=True, eof=(i == len(bin_records) - 1))
                for i, rec in enumerate(bin_records)
            ),
            "Fixed-width binary records packed inside Mode B blocks with EOR descriptor flags.",
        ),
        (
            "I", "R", "C",
            "type-I_stru-R_mode-C_binary-record-compressed.dat",
            make_mode_c_compressed(bin_rec_combined),
            "Binary structured records compressed with Mode C run-length encoding.",
        ),
        (
            "I", "P", "S",
            "type-I_stru-P_mode-S_binary-page-stream.dat",
            bin_paged_data,
            "Indexed binary page structure per RFC 959 Section 3.1.2.3 with binary page headers.",
        ),
        (
            "I", "P", "B",
            "type-I_stru-P_mode-B_binary-page-block.dat",
            make_mode_b_block(bin_paged_data, eof=True),
            "Binary pages encapsulated inside RFC 959 Mode B transmission blocks.",
        ),
        (
            "I", "P", "C",
            "type-I_stru-P_mode-C_binary-page-compressed.dat",
            make_mode_c_compressed(bin_paged_data),
            "Binary page structure with Mode C compressed page payloads.",
        ),

        # --- TYPE E (EBCDIC) ---
        (
            "E", "F", "S",
            "type-E_stru-F_mode-S_ebcdic-file-stream.ebcdic",
            ebcdic_text,
            "Authentic IBM CP037 EBCDIC mainframe text stream file.",
        ),
        (
            "E", "F", "B",
            "type-E_stru-F_mode-B_ebcdic-file-block.dat",
            make_mode_b_block(ebcdic_text, eof=True),
            "CP037 EBCDIC text stream partitioned into RFC 959 Mode B transmission blocks.",
        ),
        (
            "E", "F", "C",
            "type-E_stru-F_mode-C_ebcdic-file-compressed.dat",
            make_mode_c_compressed(ebcdic_text),
            "EBCDIC text stream compressed with run-length byte compression.",
        ),
        (
            "E", "R", "S",
            "type-E_stru-R_mode-S_ebcdic-record-stream.ebcdic",
            ebcdic_rec_combined,
            "Classic 80-column IBM mainframe punched-card records in CP037 EBCDIC.",
        ),
        (
            "E", "R", "B",
            "type-E_stru-R_mode-B_ebcdic-record-block.dat",
            b"".join(
                make_mode_b_block(rec, eor=True, eof=(i == len(ebcdic_records) - 1))
                for i, rec in enumerate(ebcdic_records)
            ),
            "80-column EBCDIC punched card records inside Mode B blocks with EOR descriptors.",
        ),
        (
            "E", "R", "C",
            "type-E_stru-R_mode-C_ebcdic-record-compressed.dat",
            make_mode_c_compressed(ebcdic_rec_combined),
            "EBCDIC card records compressed with repeated byte/blank compression.",
        ),
        (
            "E", "P", "S",
            "type-E_stru-P_mode-S_ebcdic-page-stream.ebcdic",
            ebcdic_paged_data,
            "EBCDIC mainframe report pages with RFC 959 page headers and CP037 body text.",
        ),
        (
            "E", "P", "B",
            "type-E_stru-P_mode-B_ebcdic-page-block.dat",
            make_mode_b_block(ebcdic_paged_data, eof=True),
            "EBCDIC report pages inside RFC 959 Mode B transmission blocks.",
        ),
        (
            "E", "P", "C",
            "type-E_stru-P_mode-C_ebcdic-page-compressed.dat",
            make_mode_c_compressed(ebcdic_paged_data),
            "EBCDIC report pages with Mode C compressed payloads.",
        ),
    ]

    for type_code, stru_code, mode_code, filename, content, desc in permutations:
        file_path = SAMPLES_DIR / filename
        file_path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        manifest.append({
            "type": type_code,
            "stru": stru_code,
            "mode": mode_code,
            "filename": filename,
            "relative_path": f"samples/{filename}",
            "size_bytes": len(content),
            "sha256": digest,
            "description": desc,
        })
        print(f"  [+] {filename:48s} | {len(content):5d} B | TYPE {type_code} STRU {stru_code} MODE {mode_code}")

    # Write manifest.json
    manifest_path = SAMPLES_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {manifest_path} ({len(manifest)} items)")

    # Write samples/README.md
    readme_lines = [
        "# RFC 959 TYPE / STRU / MODE Permutations Demo Suite",
        "",
        "This directory contains 27 sample files covering every valid permutation of the",
        "RFC 959 Data Representation (TYPE), File Structure (STRU), and Transmission Mode (MODE)",
        "parameters supported by this FTP implementation.",
        "",
        "| # | TYPE | STRU | MODE | Filename | Size (B) | Description |",
        "| -: | :---: | :---: | :---: | :--- | ---: | :--- |",
    ]
    for i, item in enumerate(manifest, 1):
        readme_lines.append(
            f"| {i} | `{item['type']}` | `{item['stru']}` | `{item['mode']}` | "
            f"[`{item['filename']}`]({item['filename']}) | {item['size_bytes']} | {item['description']} |"
        )
    readme_lines.extend([
        "",
        "## How to demonstrate each permutation in the terminal",
        "",
        "Connect using the FTP terminal client:",
        "```bash",
        "cd Group_Project/FTP",
        "FTP_PASSWORD=lab-demo python3 -m ftp_lab.client --trace",
        "```",
        "",
        "At the `ftp>` prompt, navigate to `samples` and select your parameters:",
        "```text",
        "cd samples",
        "ls",
        "",
        "# 1. Select representation type: A (ASCII), I (Image/Binary), or E (EBCDIC)",
        "type A",
        "# 2. Select file structure: F (File), R (Record), or P (Page)",
        "stru F",
        "# 3. Select transmission mode: S (Stream), B (Block), or C (Compressed)",
        "mode S",
        "",
        "# 4. Verify server parameters via STAT",
        "stat",
        "",
        "# 5. Download the matching sample file",
        "get type-A_stru-F_mode-S_ascii-file-stream.txt downloads/demo_A_F_S.txt",
        "```",
        "",
        "### Inspecting EBCDIC files locally",
        "EBCDIC files (`TYPE E`) are encoded in IBM CP037. View them in decoded plaintext with Python:",
        "```bash",
        "python3 -c \"print(open('downloads/type-E_stru-F_mode-S_ebcdic-file-stream.ebcdic', 'rb').read().decode('cp037'))\"",
        "```",
        "",
        "### Inspecting Binary files locally",
        "View binary files (`TYPE I`) using `hexdump` or `xxd`:",
        "```bash",
        "xxd downloads/type-I_stru-F_mode-S_binary-file-stream.bin",
        "```",
    ])
    readme_path = SAMPLES_DIR / "README.md"
    readme_path.write_text("\n".join(readme_lines) + "\n", encoding="utf-8")
    print(f"Generated {readme_path}")


if __name__ == "__main__":
    generate_all()
