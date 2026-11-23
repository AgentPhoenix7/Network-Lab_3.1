# RFC 959 TYPE / STRU / MODE Permutations Demo Suite

This directory contains 27 sample files covering every valid permutation of the
RFC 959 Data Representation (TYPE), File Structure (STRU), and Transmission Mode (MODE)
parameters supported by this FTP implementation.

| # | TYPE | STRU | MODE | Filename | Size (B) | Description |
| -: | :---: | :---: | :---: | :--- | ---: | :--- |
| 1 | `A` | `F` | `S` | [`type-A_stru-F_mode-S_ascii-file-stream.txt`](type-A_stru-F_mode-S_ascii-file-stream.txt) | 463 | Standard ASCII text stream with continuous file structure and CRLF delimiters. |
| 2 | `A` | `F` | `B` | [`type-A_stru-F_mode-B_ascii-file-block.dat`](type-A_stru-F_mode-B_ascii-file-block.dat) | 469 | ASCII text framed into RFC 959 Mode B transmission blocks with byte-count headers. |
| 3 | `A` | `F` | `C` | [`type-A_stru-F_mode-C_ascii-file-compressed.dat`](type-A_stru-F_mode-C_ascii-file-compressed.dat) | 409 | ASCII text compressed using RFC 959 Section 3.4.3 run-length compression byte counts. |
| 4 | `A` | `R` | `S` | [`type-A_stru-R_mode-S_ascii-record-stream.csv`](type-A_stru-R_mode-S_ascii-record-stream.csv) | 375 | ASCII record stream (CSV rows) where each line represents an RFC 959 sequential record. |
| 5 | `A` | `R` | `B` | [`type-A_stru-R_mode-B_ascii-record-block.dat`](type-A_stru-R_mode-B_ascii-record-block.dat) | 393 | ASCII records packed into Mode B blocks with EOR descriptor bit set per record. |
| 6 | `A` | `R` | `C` | [`type-A_stru-R_mode-C_ascii-record-compressed.dat`](type-A_stru-R_mode-C_ascii-record-compressed.dat) | 381 | Sequential ASCII records encoded with Mode C run-length space/byte compression. |
| 7 | `A` | `P` | `S` | [`type-A_stru-P_mode-S_ascii-page-stream.txt`](type-A_stru-P_mode-S_ascii-page-stream.txt) | 373 | Indexed ASCII page structure with RFC 959 page headers (page type, number, length). |
| 8 | `A` | `P` | `B` | [`type-A_stru-P_mode-B_ascii-page-block.dat`](type-A_stru-P_mode-B_ascii-page-block.dat) | 376 | Paged ASCII data encapsulated inside RFC 959 Mode B transmission blocks. |
| 9 | `A` | `P` | `C` | [`type-A_stru-P_mode-C_ascii-page-compressed.dat`](type-A_stru-P_mode-C_ascii-page-compressed.dat) | 377 | Paged ASCII data with Mode C compressed page payloads. |
| 10 | `I` | `F` | `S` | [`type-I_stru-F_mode-S_binary-file-stream.bin`](type-I_stru-F_mode-S_binary-file-stream.bin) | 303 | Byte-exact raw binary data containing all 256 byte values and binary structs. |
| 11 | `I` | `F` | `B` | [`type-I_stru-F_mode-B_binary-file-block.dat`](type-I_stru-F_mode-B_binary-file-block.dat) | 309 | Binary payload framed into RFC 959 Mode B transmission blocks with block headers. |
| 12 | `I` | `F` | `C` | [`type-I_stru-F_mode-C_binary-file-compressed.gz`](type-I_stru-F_mode-C_binary-file-compressed.gz) | 326 | Genuine gzip compressed binary stream conforming to RFC 959 compressed binary mode. |
| 13 | `I` | `R` | `S` | [`type-I_stru-R_mode-S_binary-record-stream.dat`](type-I_stru-R_mode-S_binary-record-stream.dat) | 256 | Binary record stream containing fixed 32-byte C-struct sensor records. |
| 14 | `I` | `R` | `B` | [`type-I_stru-R_mode-B_binary-record-block.dat`](type-I_stru-R_mode-B_binary-record-block.dat) | 280 | Fixed-width binary records packed inside Mode B blocks with EOR descriptor flags. |
| 15 | `I` | `R` | `C` | [`type-I_stru-R_mode-C_binary-record-compressed.dat`](type-I_stru-R_mode-C_binary-record-compressed.dat) | 226 | Binary structured records compressed with Mode C run-length encoding. |
| 16 | `I` | `P` | `S` | [`type-I_stru-P_mode-S_binary-page-stream.dat`](type-I_stru-P_mode-S_binary-page-stream.dat) | 303 | Indexed binary page structure per RFC 959 Section 3.1.2.3 with binary page headers. |
| 17 | `I` | `P` | `B` | [`type-I_stru-P_mode-B_binary-page-block.dat`](type-I_stru-P_mode-B_binary-page-block.dat) | 306 | Binary pages encapsulated inside RFC 959 Mode B transmission blocks. |
| 18 | `I` | `P` | `C` | [`type-I_stru-P_mode-C_binary-page-compressed.dat`](type-I_stru-P_mode-C_binary-page-compressed.dat) | 307 | Binary page structure with Mode C compressed page payloads. |
| 19 | `E` | `F` | `S` | [`type-E_stru-F_mode-S_ebcdic-file-stream.ebcdic`](type-E_stru-F_mode-S_ebcdic-file-stream.ebcdic) | 158 | Authentic IBM CP037 EBCDIC mainframe text stream file. |
| 20 | `E` | `F` | `B` | [`type-E_stru-F_mode-B_ebcdic-file-block.dat`](type-E_stru-F_mode-B_ebcdic-file-block.dat) | 161 | CP037 EBCDIC text stream partitioned into RFC 959 Mode B transmission blocks. |
| 21 | `E` | `F` | `C` | [`type-E_stru-F_mode-C_ebcdic-file-compressed.dat`](type-E_stru-F_mode-C_ebcdic-file-compressed.dat) | 161 | EBCDIC text stream compressed with run-length byte compression. |
| 22 | `E` | `R` | `S` | [`type-E_stru-R_mode-S_ebcdic-record-stream.ebcdic`](type-E_stru-R_mode-S_ebcdic-record-stream.ebcdic) | 444 | Classic 80-column IBM mainframe punched-card records in CP037 EBCDIC. |
| 23 | `E` | `R` | `B` | [`type-E_stru-R_mode-B_ebcdic-record-block.dat`](type-E_stru-R_mode-B_ebcdic-record-block.dat) | 462 | 80-column EBCDIC punched card records inside Mode B blocks with EOR descriptors. |
| 24 | `E` | `R` | `C` | [`type-E_stru-R_mode-C_ebcdic-record-compressed.dat`](type-E_stru-R_mode-C_ebcdic-record-compressed.dat) | 209 | EBCDIC card records compressed with repeated byte/blank compression. |
| 25 | `E` | `P` | `S` | [`type-E_stru-P_mode-S_ebcdic-page-stream.ebcdic`](type-E_stru-P_mode-S_ebcdic-page-stream.ebcdic) | 197 | EBCDIC mainframe report pages with RFC 959 page headers and CP037 body text. |
| 26 | `E` | `P` | `B` | [`type-E_stru-P_mode-B_ebcdic-page-block.dat`](type-E_stru-P_mode-B_ebcdic-page-block.dat) | 200 | EBCDIC report pages inside RFC 959 Mode B transmission blocks. |
| 27 | `E` | `P` | `C` | [`type-E_stru-P_mode-C_ebcdic-page-compressed.dat`](type-E_stru-P_mode-C_ebcdic-page-compressed.dat) | 201 | EBCDIC report pages with Mode C compressed payloads. |

## How to demonstrate each permutation in the terminal

Start the server as described in the [main README](../../README.md), then
connect using the FTP terminal client in the locked uv environment:
```bash
cd Group_Project/FTP
uv sync --locked --no-dev
FTP_PASSWORD=lab-demo uv run --locked --no-dev python -m ftp_lab.client --trace
```

At the `ftp>` prompt, navigate to `samples` and select your parameters:
```text
cd samples
ls

# 1. Select representation type: A (ASCII), I (Image/Binary), or E (EBCDIC)
type A
# 2. Select file structure: F (File), R (Record), or P (Page)
stru F
# 3. Select transmission mode: S (Stream), B (Block), or C (Compressed)
mode S

# 4. Verify server parameters via STAT
stat

# 5. Download the matching sample file
get type-A_stru-F_mode-S_ascii-file-stream.txt downloads/demo_A_F_S.txt
```

### Inspecting EBCDIC files locally
EBCDIC files (`TYPE E`) are encoded in IBM CP037. View them in decoded plaintext with Python:
```bash
uv run --locked --no-dev python -c "print(open('downloads/type-E_stru-F_mode-S_ebcdic-file-stream.ebcdic', 'rb').read().decode('cp037'))"
```

### Inspecting Binary files locally
View binary files (`TYPE I`) using `hexdump` or `xxd`:
```bash
xxd downloads/type-I_stru-F_mode-S_binary-file-stream.bin
```
