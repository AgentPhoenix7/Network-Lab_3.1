/* Relay: offline, dependency-free browser client. */
(() => {
  'use strict';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  function formatBytes(value) {
    if (!finite(value) || value < 0) return '—';
    if (value === 0) return '0 B';
    const unit = Math.max(0, Math.min(4, Math.floor(Math.log(value) / Math.log(1024))));
    return `${Number((value / 1024 ** unit).toFixed(1))} ${['B', 'KiB', 'MiB', 'GiB', 'TiB'][unit]}`;
  }
  function formatDuration(value) {
    if (!finite(value) || value < 0) return '—';
    const seconds = Math.round(value);
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  }
  function joinPath(directory, name) { return `${directory.replace(/\/+$/, '')}/${name}`; }
  function parentPath(path) { return path.replace(/\/+$/, '').split('/').slice(0, -1).join('/') || '/'; }
  function throughputSeries(samples) {
    const series = {plain: [], gzip: []};
    for (const sample of Array.isArray(samples) ? samples : []) {
      if (series[sample.mode] && finite(sample.elapsed) && sample.elapsed >= 0 && finite(sample.speed_bps) && sample.speed_bps >= 0) {
        series[sample.mode].push({x: sample.elapsed, y: sample.speed_bps / 1048576});
      }
    }
    for (const values of Object.values(series)) values.sort((a, b) => a.x - b.x);
    return series;
  }
  function comparisonMetrics(result) {
    const runs = result?.runs || [];
    const plain = runs.find(run => run.mode === 'plain');
    const gzip = runs.find(run => run.mode === 'gzip');
    return {
      reduction: gzip && finite(gzip.source_bytes) && gzip.source_bytes > 0 && finite(gzip.payload_bytes) ? Math.round((1 - gzip.payload_bytes / gzip.source_bytes) * 1000) / 10 : null,
      timeSaved: plain && gzip && finite(plain.total_seconds) && finite(gzip.total_seconds) ? plain.total_seconds - gzip.total_seconds : null,
      verified: Boolean(plain?.verified && gzip?.verified),
    };
  }
  function benchmarkRowValues(run) {
    const seconds = value => finite(value) ? `${value.toFixed(3)}s` : '—';
    const speed = value => finite(value) ? `${value.toFixed(2)} MiB/s` : '—';
    return [run.mode === 'plain' ? 'Original' : 'Gzip', formatBytes(run.payload_bytes), seconds(run.preparation_seconds), seconds(run.transfer_seconds), seconds(run.total_seconds), speed(run.throughput_mib_s), speed(run.effective_mib_s), run.verified ? 'Yes' : 'No'];
  }
  function downloadPresentation(job) {
    const completed = job.kind === 'download' && job.status === 'completed';
    const name = completed && job.result?.filename ? job.result.filename : job.name;
    const unpacked = completed && job.unpack;
    return {
      name,
      detail: unpacked ? `Unpacked from ${job.name} · ${formatBytes(job.result?.bytes)} ready to save` : '',
      saveLabel: unpacked ? 'Save unpacked file ↓' : 'Save file ↓',
    };
  }
  function createSessionGate() {
    let version = 0;
    return {
      invalidate() { version++; },
      async protect(promise) {
        const expected = version;
        try {
          const result = await promise;
          if (expected === version) return result;
        } catch (error) {
          if (expected === version) throw error;
        }
        const error = new Error('Your session changed. Please try again.');
        error.stale = true;
        throw error;
      },
    };
  }
  const commandCatalog = {
    USER: {
      verb: 'USER', name: 'User Name', category: 'access', rfc: 'RFC 959 § 4.1.1',
      syntax: 'USER <SP> <username> <CRLF>', channel: 'control', hasArg: true, defaultArg: 'student',
      description: 'Identifies the user to the server. If a password is required, the server returns 331.',
      replies: ['331 Password required', '230 Logged in', '530 Not logged in', '500 Syntax error'],
      run(arg) {
        return [
          { sender: 'client', text: `USER ${arg || 'student'}`, channel: 'control' },
          { sender: 'server', text: '331 Password required', channel: 'control', code: 331 }
        ];
      }
    },
    PASS: {
      verb: 'PASS', name: 'Password', category: 'access', rfc: 'RFC 959 § 4.1.1',
      syntax: 'PASS <SP> <password> <CRLF>', channel: 'control', hasArg: true, defaultArg: 'lab-test',
      description: 'Provides the authentication password for the preceding USER command. Returns 230 on success.',
      replies: ['230 Login successful', '530 Login incorrect', '503 Send USER first'],
      run(arg) {
        return [
          { sender: 'client', text: `PASS ${'*'.repeat((arg || 'lab-test').length)}`, channel: 'control' },
          { sender: 'server', text: '230 Login successful', channel: 'control', code: 230 }
        ];
      }
    },
    ACCT: {
      verb: 'ACCT', name: 'Account Info', category: 'access', rfc: 'RFC 959 § 4.1.1',
      syntax: 'ACCT <SP> <account-info> <CRLF>', channel: 'control', hasArg: true, defaultArg: 'cs-lab',
      description: 'Identifies the user account for bookkeeping. Replies 202 if accounting is not required.',
      replies: ['202 Command not implemented, superfluous at this site', '230 Logged in', '530 Not logged in'],
      run(arg) {
        return [
          { sender: 'client', text: `ACCT ${arg || 'cs-lab'}`, channel: 'control' },
          { sender: 'server', text: '202 Command not implemented, superfluous at this site', channel: 'control', code: 202 }
        ];
      }
    },
    CWD: {
      verb: 'CWD', name: 'Change Working Directory', category: 'dir', rfc: 'RFC 959 § 4.1.1',
      syntax: 'CWD <SP> <pathname> <CRLF>', channel: 'control', hasArg: true, defaultArg: 'documents',
      description: 'Changes the current directory within the virtual sandbox root. Returns 250 on success.',
      replies: ['250 Directory changed', '550 Directory unavailable or forbidden'],
      run(arg) {
        return [
          { sender: 'client', text: `CWD ${arg || 'documents'}`, channel: 'control' },
          { sender: 'server', text: '250 Directory changed', channel: 'control', code: 250 }
        ];
      }
    },
    CDUP: {
      verb: 'CDUP', name: 'Change to Parent Directory', category: 'dir', rfc: 'RFC 959 § 4.1.1',
      syntax: 'CDUP <CRLF>', channel: 'control', hasArg: false, defaultArg: '',
      description: 'Navigates to the parent directory (..) in the virtual sandbox. Returns 250.',
      replies: ['250 Directory changed', '550 Path escapes FTP root'],
      run() {
        return [
          { sender: 'client', text: 'CDUP', channel: 'control' },
          { sender: 'server', text: '250 Directory changed', channel: 'control', code: 250 }
        ];
      }
    },
    SMNT: {
      verb: 'SMNT', name: 'Structure Mount', category: 'access', rfc: 'RFC 959 § 4.1.1',
      syntax: 'SMNT <SP> <pathname> <CRLF>', channel: 'control', hasArg: true, defaultArg: '/media/vol1',
      description: 'Mounts an alternative file system structure without altering login. Standard POSIX servers reply 502.',
      replies: ['502 Command not implemented', '202 Superfluous at this site'],
      run(arg) {
        return [
          { sender: 'client', text: `SMNT ${arg || '/media/vol1'}`, channel: 'control' },
          { sender: 'server', text: '502 Command not implemented', channel: 'control', code: 502 }
        ];
      }
    },
    REIN: {
      verb: 'REIN', name: 'Reinitialize Session', category: 'access', rfc: 'RFC 959 § 4.1.1',
      syntax: 'REIN <CRLF>', channel: 'control', hasArg: false, defaultArg: '',
      description: 'Flushes authentication and resets all transfer parameters to standard defaults without dropping the control connection.',
      replies: ['220 Service ready for new user', '421 Service not available'],
      run() {
        return [
          { sender: 'client', text: 'REIN', channel: 'control' },
          { sender: 'server', text: '220 Service ready for new user', channel: 'control', code: 220 }
        ];
      }
    },
    QUIT: {
      verb: 'QUIT', name: 'Logout & Disconnect', category: 'access', rfc: 'RFC 959 § 4.1.1',
      syntax: 'QUIT <CRLF>', channel: 'control', hasArg: false, defaultArg: '',
      description: 'Terminates the session and cleanly closes the control connection. Server replies 221 Goodbye.',
      replies: ['221 Goodbye', '500 Syntax error'],
      run() {
        return [
          { sender: 'client', text: 'QUIT', channel: 'control' },
          { sender: 'server', text: '221 Goodbye', channel: 'control', code: 221 }
        ];
      }
    },
    PORT: {
      verb: 'PORT', name: 'Active Data Port', category: 'params', rfc: 'RFC 959 § 4.1.2',
      syntax: 'PORT <SP> h1,h2,h3,h4,p1,p2 <CRLF>', channel: 'control', hasArg: true, defaultArg: '127,0,0,1,192,44',
      description: 'Specifies the host IP and TCP port to which the server must connect for active data transfers.',
      replies: ['200 Active data endpoint set', '501 Invalid endpoint or bounce attempt'],
      run(arg) {
        return [
          { sender: 'client', text: `PORT ${arg || '127,0,0,1,192,44'}`, channel: 'control' },
          { sender: 'server', text: '200 Active data endpoint set', channel: 'control', code: 200 }
        ];
      }
    },
    PASV: {
      verb: 'PASV', name: 'Passive Mode', category: 'params', rfc: 'RFC 959 § 4.1.2',
      syntax: 'PASV <CRLF>', channel: 'control', hasArg: false, defaultArg: '',
      description: 'Requests server-DTP to listen on an ephemeral data port and wait for client connection. Essential for firewalls and NAT.',
      replies: ['227 Entering Passive Mode (127,0,0,1,192,44)', '421 Service closing'],
      run() {
        const port = 49152 + Math.floor(Math.random() * 1000);
        const p1 = Math.floor(port / 256), p2 = port % 256;
        return [
          { sender: 'client', text: 'PASV', channel: 'control' },
          { sender: 'server', text: `227 Entering Passive Mode (127,0,0,1,${p1},${p2})`, channel: 'control', code: 227, dataPort: port }
        ];
      }
    },
    TYPE: {
      verb: 'TYPE', name: 'Representation Type', category: 'params', rfc: 'RFC 959 § 4.1.2',
      syntax: 'TYPE <SP> <A|I|E|L 8> <CRLF>', channel: 'control', hasArg: true, defaultArg: 'I',
      description: 'Sets data representation: ASCII Non-print (A), Image/Binary (I), EBCDIC (E), or Local byte size (L 8).',
      replies: ['200 Transfer type set', '504 Parameter not supported'],
      run(arg) {
        const t = (arg || 'I').toUpperCase();
        return [
          { sender: 'client', text: `TYPE ${t}`, channel: 'control' },
          { sender: 'server', text: '200 Transfer type set', channel: 'control', code: 200 }
        ];
      }
    },
    STRU: {
      verb: 'STRU', name: 'File Structure', category: 'params', rfc: 'RFC 959 § 4.1.2',
      syntax: 'STRU <SP> <F|R|P> <CRLF>', channel: 'control', hasArg: true, defaultArg: 'F',
      description: 'Sets structure: File (F - continuous stream, default), Record (R), or Page (P).',
      replies: ['200 Structure set to F', '504 Parameter not supported'],
      run(arg) {
        const s = (arg || 'F').toUpperCase();
        return [
          { sender: 'client', text: `STRU ${s}`, channel: 'control' },
          { sender: 'server', text: `200 Structure set to ${s}`, channel: 'control', code: 200 }
        ];
      }
    },
    MODE: {
      verb: 'MODE', name: 'Transmission Mode', category: 'params', rfc: 'RFC 959 § 4.1.2',
      syntax: 'MODE <SP> <S|B|C> <CRLF>', channel: 'control', hasArg: true, defaultArg: 'S',
      description: 'Sets transfer mode: Stream (S - EOF on close, default), Block (B), or Compressed (C).',
      replies: ['200 Mode set to S', '504 Parameter not supported'],
      run(arg) {
        const m = (arg || 'S').toUpperCase();
        return [
          { sender: 'client', text: `MODE ${m}`, channel: 'control' },
          { sender: 'server', text: `200 Mode set to ${m}`, channel: 'control', code: 200 }
        ];
      }
    },
    RETR: {
      verb: 'RETR', name: 'Retrieve (Download)', category: 'file', rfc: 'RFC 959 § 4.1.3',
      syntax: 'RETR <SP> <pathname> <CRLF>', channel: 'data-download', hasArg: true, defaultArg: 'dataset.csv',
      description: 'Transfers a file from server to client over the ephemeral data connection.',
      replies: ['150 Opening data connection', '226 Transfer complete', '425 No data endpoint', '426 Connection closed; aborted', '451 Local error in processing', '550 File not found'],
      run(arg) {
        const file = arg || 'dataset.csv';
        return [
          { sender: 'client', text: `RETR ${file}`, channel: 'control' },
          { sender: 'server', text: '150 Opening data connection', channel: 'control', code: 150 },
          { sender: 'data-server', text: `[DATA STREAM] Downloading ${file} (65,536 bytes) -> Client`, channel: 'data', dir: 'download', bytes: 65536 },
          { sender: 'server', text: '226 Transfer complete', channel: 'control', code: 226 }
        ];
      }
    },
    STOR: {
      verb: 'STOR', name: 'Store (Upload)', category: 'file', rfc: 'RFC 959 § 4.1.3',
      syntax: 'STOR <SP> <pathname> <CRLF>', channel: 'data-upload', hasArg: true, defaultArg: 'report.pdf',
      description: 'Transfers a file from client to server. Atomically staged so interruptions leave original files intact.',
      replies: ['150 Opening data connection', '226 Transfer complete', '425 No data endpoint', '452 Insufficient storage space', '552 Exceeded storage allocation', '553 File name not allowed', '550 Access forbidden'],
      run(arg) {
        const file = arg || 'report.pdf';
        return [
          { sender: 'client', text: `STOR ${file}`, channel: 'control' },
          { sender: 'server', text: '150 Opening data connection', channel: 'control', code: 150 },
          { sender: 'data-client', text: `[DATA STREAM] Uploading ${file} (128,400 bytes) -> Server`, channel: 'data', dir: 'upload', bytes: 128400 },
          { sender: 'server', text: '226 Transfer complete', channel: 'control', code: 226 }
        ];
      }
    },
    STOU: {
      verb: 'STOU', name: 'Store Unique', category: 'file', rfc: 'RFC 959 § 4.1.3',
      syntax: 'STOU <CRLF>', channel: 'data-upload', hasArg: false, defaultArg: '',
      description: 'Creates a unique unused filename on the server and stores incoming data. The 150/250 reply reports the generated name.',
      replies: ['150 FILE: file_7a3d82f1', '250 Transfer complete (unique file: file_7a3d82f1)', '425 No data connection'],
      run() {
        const unique = 'file_' + Math.random().toString(16).slice(2, 10);
        return [
          { sender: 'client', text: 'STOU', channel: 'control' },
          { sender: 'server', text: `150 FILE: ${unique}`, channel: 'control', code: 150 },
          { sender: 'data-client', text: `[DATA STREAM] Uploading unique file payload (${unique})...`, channel: 'data', dir: 'upload', bytes: 32768 },
          { sender: 'server', text: `250 Transfer complete (unique file: ${unique})`, channel: 'control', code: 250 }
        ];
      }
    },
    APPE: {
      verb: 'APPE', name: 'Append (with create)', category: 'file', rfc: 'RFC 959 § 4.1.3',
      syntax: 'APPE <SP> <pathname> <CRLF>', channel: 'data-upload', hasArg: true, defaultArg: 'access.log',
      description: 'Appends data to an existing regular file, or creates it if missing. Staged atomically to protect original contents.',
      replies: ['150 Opening data connection', '226 Transfer complete', '425 No data connection', '550 File unavailable'],
      run(arg) {
        const file = arg || 'access.log';
        return [
          { sender: 'client', text: `APPE ${file}`, channel: 'control' },
          { sender: 'server', text: '150 Opening data connection', channel: 'control', code: 150 },
          { sender: 'data-client', text: `[DATA STREAM] Appending 4,096 bytes to ${file}...`, channel: 'data', dir: 'upload', bytes: 4096 },
          { sender: 'server', text: '226 Transfer complete', channel: 'control', code: 226 }
        ];
      }
    },
    ALLO: {
      verb: 'ALLO', name: 'Allocate Storage', category: 'file', rfc: 'RFC 959 § 4.1.3',
      syntax: 'ALLO <SP> <bytes> [<SP> R <SP> <record-size>] <CRLF>', channel: 'control', hasArg: true, defaultArg: '1048576',
      description: 'Pre-allocates storage on systems requiring it. Dynamic POSIX filesystems treat it as a NOOP and reply 202.',
      replies: ['202 Command not implemented, superfluous at this site', '200 OK'],
      run(arg) {
        return [
          { sender: 'client', text: `ALLO ${arg || '1048576'}`, channel: 'control' },
          { sender: 'server', text: '202 Command not implemented, superfluous at this site', channel: 'control', code: 202 }
        ];
      }
    },
    REST: {
      verb: 'REST', name: 'Restart Marker', category: 'file', rfc: 'RFC 959 § 4.1.3',
      syntax: 'REST <SP> <marker> <CRLF>', channel: 'control', hasArg: true, defaultArg: '1024',
      description: 'Sets a byte marker for restarting an interrupted transfer. Standard stream mode replies 502.',
      replies: ['502 Command not implemented', '350 Restarting at marker'],
      run(arg) {
        return [
          { sender: 'client', text: `REST ${arg || '1024'}`, channel: 'control' },
          { sender: 'server', text: '502 Command not implemented', channel: 'control', code: 502 }
        ];
      }
    },
    DELE: {
      verb: 'DELE', name: 'Delete File', category: 'file', rfc: 'RFC 959 § 4.1.3',
      syntax: 'DELE <SP> <pathname> <CRLF>', channel: 'control', hasArg: true, defaultArg: 'temp.log',
      description: 'Removes the specified regular file from the server sandbox. Rejects symlinks and directories.',
      replies: ['250 Removed', '550 File unavailable or forbidden'],
      run(arg) {
        return [
          { sender: 'client', text: `DELE ${arg || 'temp.log'}`, channel: 'control' },
          { sender: 'server', text: '250 Removed', channel: 'control', code: 250 }
        ];
      }
    },
    PWD: {
      verb: 'PWD', name: 'Print Working Directory', category: 'dir', rfc: 'RFC 959 Appendix II',
      syntax: 'PWD <CRLF>', channel: 'control', hasArg: false, defaultArg: '',
      description: 'Returns the current working directory in double-quotes, with quote-doubling escaping per Appendix II.',
      replies: ['257 "/" is current directory', '550 File unavailable'],
      run() {
        return [
          { sender: 'client', text: 'PWD', channel: 'control' },
          { sender: 'server', text: '257 "/"', channel: 'control', code: 257 }
        ];
      }
    },
    MKD: {
      verb: 'MKD', name: 'Make Directory', category: 'dir', rfc: 'RFC 959 Appendix II',
      syntax: 'MKD <SP> <pathname> <CRLF>', channel: 'control', hasArg: true, defaultArg: 'archives',
      description: 'Creates a new subdirectory within the virtual sandbox. Returns 257 "<path>" created.',
      replies: ['257 "/archives" created', '550 Directory exists or invalid'],
      run(arg) {
        const d = arg || 'archives';
        return [
          { sender: 'client', text: `MKD ${d}`, channel: 'control' },
          { sender: 'server', text: `257 "/${d}" created`, channel: 'control', code: 257 }
        ];
      }
    },
    RMD: {
      verb: 'RMD', name: 'Remove Directory', category: 'dir', rfc: 'RFC 959 Appendix II',
      syntax: 'RMD <SP> <pathname> <CRLF>', channel: 'control', hasArg: true, defaultArg: 'archives',
      description: 'Removes an empty directory. Rejects non-empty directories and root.',
      replies: ['250 Removed', '550 Directory not empty or not found'],
      run(arg) {
        return [
          { sender: 'client', text: `RMD ${arg || 'archives'}`, channel: 'control' },
          { sender: 'server', text: '250 Removed', channel: 'control', code: 250 }
        ];
      }
    },
    LIST: {
      verb: 'LIST', name: 'Directory Listing', category: 'dir', rfc: 'RFC 959 § 4.1.3',
      syntax: 'LIST [<SP> <pathname>] <CRLF>', channel: 'data-download', hasArg: true, defaultArg: '',
      description: 'Transfers a full UNIX-style directory listing with file permissions, sizes, and timestamps over the data connection.',
      replies: ['150 Opening data connection', '226 Transfer complete', '425 No data endpoint', '550 Directory not found'],
      run(arg) {
        return [
          { sender: 'client', text: arg ? `LIST ${arg}` : 'LIST', channel: 'control' },
          { sender: 'server', text: '150 Opening data connection', channel: 'control', code: 150 },
          { sender: 'data-server', text: '[DATA STREAM]\r\n-rw------- 1 ftp ftp  1048576 Jan 01 00:00 audio.wav\r\ndrwx------ 1 ftp ftp     4096 Jan 01 00:00 documents', channel: 'data', dir: 'download', bytes: 140 },
          { sender: 'server', text: '226 Transfer complete', channel: 'control', code: 226 }
        ];
      }
    },
    NLST: {
      verb: 'NLST', name: 'Name List', category: 'dir', rfc: 'RFC 959 § 4.1.3',
      syntax: 'NLST [<SP> <pathname>] <CRLF>', channel: 'data-download', hasArg: true, defaultArg: '',
      description: 'Transfers bare filenames separated by CRLF over data connection (suitable for automated script processing).',
      replies: ['150 Opening data connection', '226 Transfer complete', '425 No data connection'],
      run(arg) {
        return [
          { sender: 'client', text: arg ? `NLST ${arg}` : 'NLST', channel: 'control' },
          { sender: 'server', text: '150 Opening data connection', channel: 'control', code: 150 },
          { sender: 'data-server', text: '[DATA STREAM] audio.wav\r\ndocuments\r\nreport.pdf', channel: 'data', dir: 'download', bytes: 32 },
          { sender: 'server', text: '226 Transfer complete', channel: 'control', code: 226 }
        ];
      }
    },
    RNFR: {
      verb: 'RNFR', name: 'Rename From', category: 'dir', rfc: 'RFC 959 § 4.1.3',
      syntax: 'RNFR <SP> <pathname> <CRLF>', channel: 'control', hasArg: true, defaultArg: 'draft.txt',
      description: 'Specifies the existing file or directory to rename. Must be immediately followed by RNTO.',
      replies: ['350 File exists, ready for destination name', '550 File not found'],
      run(arg) {
        return [
          { sender: 'client', text: `RNFR ${arg || 'draft.txt'}`, channel: 'control' },
          { sender: 'server', text: '350 File exists, ready for destination name', channel: 'control', code: 350 }
        ];
      }
    },
    RNTO: {
      verb: 'RNTO', name: 'Rename To', category: 'dir', rfc: 'RFC 959 § 4.1.3',
      syntax: 'RNTO <SP> <pathname> <CRLF>', channel: 'control', hasArg: true, defaultArg: 'final.txt',
      description: 'Specifies destination name after RNFR. Atomically renames entry using descriptor-relative replace.',
      replies: ['250 Rename successful', '503 Send RNFR first', '553 Invalid destination name'],
      run(arg) {
        return [
          { sender: 'client', text: `RNTO ${arg || 'final.txt'}`, channel: 'control' },
          { sender: 'server', text: '250 Rename successful', channel: 'control', code: 250 }
        ];
      }
    },
    STAT: {
      verb: 'STAT', name: 'Status', category: 'misc', rfc: 'RFC 959 § 4.1.3',
      syntax: 'STAT [<SP> <pathname>] <CRLF>', channel: 'control', hasArg: true, defaultArg: '',
      description: 'Returns server status or file details directly over control connection without opening a data connection.',
      replies: ['211-FTP server status', '212-Directory status', '213-File status', '450 File busy', '550 File unavailable'],
      run(arg) {
        if (arg) {
          const isDir = arg.endsWith('/') || arg === '.' || arg === 'archives' || arg === 'documents';
          const code = isDir ? 212 : 213;
          const kind = isDir ? 'directory' : 'file';
          return [
            { sender: 'client', text: `STAT ${arg}`, channel: 'control' },
            { sender: 'server', text: `${code}-Status of ${kind} ${arg}:\r\n -rw-r--r-- 1 ftp ftp 40960 Jan 01 00:00 ${arg}\r\n${code} End of status`, channel: 'control', code }
          ];
        }
        return [
          { sender: 'client', text: 'STAT', channel: 'control' },
          { sender: 'server', text: '211-FTP server status:\r\n Connected from 127.0.0.1:54320\r\n Logged in as: student\r\n TYPE: I, STRU: F, MODE: S\r\n Data: Passive listening\r\n211 End of status', channel: 'control', code: 211 }
        ];
      }
    },
    SYST: {
      verb: 'SYST', name: 'System Type', category: 'misc', rfc: 'RFC 959 § 4.1.3',
      syntax: 'SYST <CRLF>', channel: 'control', hasArg: false, defaultArg: '',
      description: 'Returns operating system identifier. Reply format: 215 UNIX Type: L8.',
      replies: ['215 UNIX Type: L8', '500 Syntax error'],
      run() {
        return [
          { sender: 'client', text: 'SYST', channel: 'control' },
          { sender: 'server', text: '215 UNIX Type: L8', channel: 'control', code: 215 }
        ];
      }
    },
    HELP: {
      verb: 'HELP', name: 'Help', category: 'misc', rfc: 'RFC 959 § 4.1.3',
      syntax: 'HELP [<SP> <command>] <CRLF>', channel: 'control', hasArg: true, defaultArg: '',
      description: 'Returns helpful information and lists all supported wire commands.',
      replies: ['214 Command list', '500 Syntax error'],
      run() {
        return [
          { sender: 'client', text: 'HELP', channel: 'control' },
          { sender: 'server', text: '214 USER PASS ACCT CWD CDUP SMNT REIN QUIT PORT PASV TYPE STRU MODE RETR STOR STOU APPE ALLO REST RNFR RNTO ABOR DELE RMD MKD PWD LIST NLST SITE SYST STAT HELP NOOP EPSV EPRT SIZE FEAT OPTS', channel: 'control', code: 214 }
        ];
      }
    },
    NOOP: {
      verb: 'NOOP', name: 'No Operation', category: 'misc', rfc: 'RFC 959 § 4.1.3',
      syntax: 'NOOP <CRLF>', channel: 'control', hasArg: false, defaultArg: '',
      description: 'Specifies no action other than server returning 200 OK. Used as keep-alive check.',
      replies: ['200 OK', '421 Service closing'],
      run() {
        return [
          { sender: 'client', text: 'NOOP', channel: 'control' },
          { sender: 'server', text: '200 OK', channel: 'control', code: 200 }
        ];
      }
    },
    SITE: {
      verb: 'SITE', name: 'Site Parameters', category: 'misc', rfc: 'RFC 959 § 4.1.3',
      syntax: 'SITE <SP> <string> <CRLF>', channel: 'control', hasArg: true, defaultArg: 'CHMOD 644 file.txt',
      description: 'Site-specific parameters and utilities. Replies 202 if superfluous.',
      replies: ['202 Command not implemented, superfluous at this site', '200 OK'],
      run(arg) {
        return [
          { sender: 'client', text: `SITE ${arg || 'CHMOD 644 file.txt'}`, channel: 'control' },
          { sender: 'server', text: '202 Command not implemented, superfluous at this site', channel: 'control', code: 202 }
        ];
      }
    },
    ABOR: {
      verb: 'ABOR', name: 'Abort', category: 'misc', rfc: 'RFC 959 § 4.1.3',
      syntax: 'ABOR <CRLF>', channel: 'control', hasArg: false, defaultArg: '',
      description: 'Tells server to abort the previous FTP service command and close the data connection.',
      replies: ['226 Data setup cleared', '426 Transfer aborted', '500 Syntax error'],
      run() {
        return [
          { sender: 'client', text: 'ABOR', channel: 'control' },
          { sender: 'server', text: '226 Data setup cleared', channel: 'control', code: 226 }
        ];
      }
    },
    EPSV: {
      verb: 'EPSV', name: 'Extended Passive', category: 'extensions', rfc: 'RFC 2428',
      syntax: 'EPSV [<SP> <1|ALL>] <CRLF>', channel: 'control', hasArg: true, defaultArg: '1',
      description: 'Extended passive mode for IPv4/IPv6 and firewall traversal. Replies 229 (|||port|).',
      replies: ['229 Entering Extended Passive Mode (|||51234|)', '501 Bad argument', '522 Use IPv4 (1)'],
      run(arg) {
        const port = 50000 + Math.floor(Math.random() * 1000);
        return [
          { sender: 'client', text: arg ? `EPSV ${arg}` : 'EPSV', channel: 'control' },
          { sender: 'server', text: `229 Entering Extended Passive Mode (|||${port}|)`, channel: 'control', code: 229, dataPort: port }
        ];
      }
    },
    EPRT: {
      verb: 'EPRT', name: 'Extended Port', category: 'extensions', rfc: 'RFC 2428',
      syntax: 'EPRT <SP> |<proto>|<ip>|<port>| <CRLF>', channel: 'control', hasArg: true, defaultArg: '|1|127.0.0.1|54321|',
      description: 'Extended active data port specifying network protocol (1=IPv4), address and port.',
      replies: ['200 Active data endpoint set', '501 Invalid EPRT', '522 Use IPv4 (1)'],
      run(arg) {
        return [
          { sender: 'client', text: `EPRT ${arg || '|1|127.0.0.1|54321|'}`, channel: 'control' },
          { sender: 'server', text: '200 Active data endpoint set', channel: 'control', code: 200 }
        ];
      }
    },
    SIZE: {
      verb: 'SIZE', name: 'File Size', category: 'extensions', rfc: 'RFC 3659',
      syntax: 'SIZE <SP> <pathname> <CRLF>', channel: 'control', hasArg: true, defaultArg: 'audio.wav',
      description: 'Returns exact file size in bytes for binary files without opening a data connection.',
      replies: ['213 134217728', '550 File unavailable', '550 SIZE requires TYPE I'],
      run(arg) {
        return [
          { sender: 'client', text: `SIZE ${arg || 'audio.wav'}`, channel: 'control' },
          { sender: 'server', text: '213 134217728', channel: 'control', code: 213 }
        ];
      }
    },
    FEAT: {
      verb: 'FEAT', name: 'Feature Negotiation', category: 'extensions', rfc: 'RFC 2389',
      syntax: 'FEAT <CRLF>', channel: 'control', hasArg: false, defaultArg: '',
      description: 'Queries server for supported modern extensions and options.',
      replies: ['211-Features\r\n EPRT\r\n EPSV\r\n SIZE\r\n UTF8\r\n211 End', '500 Syntax error'],
      run() {
        return [
          { sender: 'client', text: 'FEAT', channel: 'control' },
          { sender: 'server', text: '211-Features\r\n EPRT\r\n EPSV\r\n SIZE\r\n UTF8\r\n211 End', channel: 'control', code: 211 }
        ];
      }
    },
    OPTS: {
      verb: 'OPTS', name: 'Options Negotiation', category: 'extensions', rfc: 'RFC 2640',
      syntax: 'OPTS <SP> UTF8 ON <CRLF>', channel: 'control', hasArg: true, defaultArg: 'UTF8 ON',
      description: 'Configures optional protocol features such as UTF-8 path and filename encoding.',
      replies: ['200 UTF8 enabled', '501 Only UTF8 ON is supported'],
      run(arg) {
        return [
          { sender: 'client', text: `OPTS ${arg || 'UTF8 ON'}`, channel: 'control' },
          { sender: 'server', text: '200 UTF8 enabled', channel: 'control', code: 200 }
        ];
      }
    }
  };
  const GUIDED_SCENARIOS = {
    auth_flow: {
      id: 'auth_flow',
      title: 'Full Authentication Handshake',
      subtitle: 'USER → PASS → SYST → FEAT',
      category: 'scenarios',
      description: 'Demonstrates RFC 959 User PI authentication followed by OS system identification (SYST) and RFC 2389 modern feature negotiation (FEAT).',
      steps: [
        { sender: 'client', text: 'USER student', channel: 'control' },
        { sender: 'server', text: '331 Password required', channel: 'control', code: 331 },
        { sender: 'client', text: 'PASS ********', channel: 'control' },
        { sender: 'server', text: '230 Login successful', channel: 'control', code: 230 },
        { sender: 'client', text: 'SYST', channel: 'control' },
        { sender: 'server', text: '215 UNIX Type: L8', channel: 'control', code: 215 },
        { sender: 'client', text: 'FEAT', channel: 'control' },
        { sender: 'server', text: '211-Features:\r\n EPRT\r\n EPSV\r\n SIZE\r\n UTF8\r\n211 End', channel: 'control', code: 211 }
      ]
    },
    pasv_upload: {
      id: 'pasv_upload',
      title: 'Passive Mode Upload (PASV + STOR)',
      subtitle: 'TYPE I → PASV → STOR',
      category: 'scenarios',
      description: 'Client requests the server to open an ephemeral listening data port (PASV), then connects to it and streams binary payload data via STOR.',
      steps: [
        { sender: 'client', text: 'TYPE I', channel: 'control' },
        { sender: 'server', text: '200 Type set to I (Image/Binary)', channel: 'control', code: 200 },
        { sender: 'client', text: 'PASV', channel: 'control' },
        { sender: 'server', text: '227 Entering Passive Mode (127,0,0,1,195,80)', channel: 'control', code: 227, dataPort: 50000 },
        { sender: 'client', text: 'STOR payload.dat', channel: 'control' },
        { sender: 'server', text: '150 Opening BINARY mode data connection for payload.dat', channel: 'control', code: 150 },
        { sender: 'data-client', text: '[DATA STREAM: 65,536 bytes transmitted to server]', channel: 'data', dir: 'upload', bytes: 65536 },
        { sender: 'server', text: '226 Transfer complete', channel: 'control', code: 226 }
      ]
    },
    port_download: {
      id: 'port_download',
      title: 'Active Mode Download (PORT + RETR)',
      subtitle: 'TYPE I → PORT → RETR',
      category: 'scenarios',
      description: 'Client opens a local listening socket and notifies the server with PORT (h1,h2,h3,h4,p1,p2). The server initiates the TCP data connection to the client and transmits the file via RETR.',
      steps: [
        { sender: 'client', text: 'TYPE I', channel: 'control' },
        { sender: 'server', text: '200 Type set to I (Image/Binary)', channel: 'control', code: 200 },
        { sender: 'client', text: 'PORT 127,0,0,1,212,48', channel: 'control' },
        { sender: 'server', text: '200 Active data endpoint set to 127.0.0.1:54320', channel: 'control', code: 200, clientPort: 54320 },
        { sender: 'client', text: 'RETR report.pdf', channel: 'control' },
        { sender: 'server', text: '150 Opening BINARY mode data connection for report.pdf (131072 bytes)', channel: 'control', code: 150 },
        { sender: 'data-server', text: '[DATA STREAM: 131,072 bytes received from server]', channel: 'data', dir: 'download', bytes: 131072 },
        { sender: 'server', text: '226 Transfer complete', channel: 'control', code: 226 }
      ]
    },
    unique_append: {
      id: 'unique_append',
      title: 'Store Unique & Append (STOU + APPE)',
      subtitle: 'PASV → STOU → PASV → APPE',
      category: 'scenarios',
      description: 'Demonstrates STOU creating a collision-free unique file on the server (150/250 FILE: <name>), followed by APPE appending subsequent records to that same file.',
      steps: [
        { sender: 'client', text: 'TYPE I', channel: 'control' },
        { sender: 'server', text: '200 Type set to I', channel: 'control', code: 200 },
        { sender: 'client', text: 'PASV', channel: 'control' },
        { sender: 'server', text: '227 Entering Passive Mode (127,0,0,1,195,81)', channel: 'control', code: 227, dataPort: 50001 },
        { sender: 'client', text: 'STOU log.txt', channel: 'control' },
        { sender: 'server', text: '150 Opening data connection for STOU', channel: 'control', code: 150 },
        { sender: 'data-client', text: '[DATA STREAM: 2,048 bytes initial log entry]', channel: 'data', dir: 'upload', bytes: 2048 },
        { sender: 'server', text: '250 FILE: log_20261122_01.txt', channel: 'control', code: 250 },
        { sender: 'client', text: 'PASV', channel: 'control' },
        { sender: 'server', text: '227 Entering Passive Mode (127,0,0,1,195,82)', channel: 'control', code: 227, dataPort: 50002 },
        { sender: 'client', text: 'APPE log_20261122_01.txt', channel: 'control' },
        { sender: 'server', text: '150 Appending to log_20261122_01.txt', channel: 'control', code: 150 },
        { sender: 'data-client', text: '[DATA STREAM: 1,024 bytes appended log entry]', channel: 'data', dir: 'upload', bytes: 1024 },
        { sender: 'server', text: '226 Transfer complete', channel: 'control', code: 226 }
      ]
    },
    rename_tx: {
      id: 'rename_tx',
      title: 'Two-Phase Atomic Rename (RNFR + RNTO)',
      subtitle: 'RNFR → RNTO',
      category: 'scenarios',
      description: 'RFC 959 two-phase transaction. RNFR locks the source target and yields 350; RNTO specifies the new target and atomically replaces it via openat2/renameat2 semantics.',
      steps: [
        { sender: 'client', text: 'RNFR draft_v1.txt', channel: 'control' },
        { sender: 'server', text: '350 File exists, ready for destination name', channel: 'control', code: 350 },
        { sender: 'client', text: 'RNTO final_release.txt', channel: 'control' },
        { sender: 'server', text: '250 Rename successful', channel: 'control', code: 250 }
      ]
    },
    session_rein: {
      id: 'session_rein',
      title: 'Session Reinitialization & Termination',
      subtitle: 'STAT → REIN → QUIT',
      category: 'scenarios',
      description: 'Queries server status over control channel (STAT), resets user PI session parameters to pristine state (REIN), and cleanly terminates the control connection (QUIT).',
      steps: [
        { sender: 'client', text: 'STAT', channel: 'control' },
        { sender: 'server', text: '211-FTP server status:\r\n Connected from 127.0.0.1:54320\r\n Logged in as: student\r\n TYPE: I, STRU: F, MODE: S\r\n211 End of status', channel: 'control', code: 211 },
        { sender: 'client', text: 'REIN', channel: 'control' },
        { sender: 'server', text: '220 Service ready for new user (state cleared)', channel: 'control', code: 220 },
        { sender: 'client', text: 'QUIT', channel: 'control' },
        { sender: 'server', text: '221 Goodbye.', channel: 'control', code: 221 }
      ]
    }
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = {formatBytes, formatDuration, joinPath, parentPath, throughputSeries, comparisonMetrics, benchmarkRowValues, downloadPresentation, createSessionGate, commandCatalog, GUIDED_SCENARIOS};
  if (typeof document === 'undefined') return;

  const $ = id => document.getElementById(id);
  const terminal = new Set(['completed', 'failed', 'cancelled']);
  const active = job => !terminal.has(job.status);
  const state = {session: null, devices: [], jobs: [], selectedId: null, path: '/', selectedFile: null, view: 'transfers', sending: false, sendGeneration: 0, staging: new Map(), sources: new Map(), requests: new Map(), filesGeneration: 0, polling: null, pollGeneration: 0, jobsGeneration: 0, benchmarkId: '', retryTarget: null, protoCategory: 'scenarios', protoCommand: 'USER', protoScenario: 'auth_flow', protoSearch: '', protoRunning: false};
  const sessionGate = createSessionGate();
  let noticeTimer;
  let confirmCallback = null;
  let connectDeviceId = null;
  let dialogOpener = null;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function icon(kind) {
    const paths = {
      device: 'M4 4h16v11H4z M8 20h8 M12 15v5',
      folder: 'M3 7h7l2-3h8v16H3z',
      file: 'M6 3h8l4 4v14H6z M14 3v5h4 M9 12h6 M9 16h6',
    };
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS(svg.namespaceURI, 'path');
    path.setAttribute('d', paths[kind] || paths.file);
    svg.append(path);
    return svg;
  }
  function actionButton(text, className, action, focusKey) {
    const button = element('button', className, text);
    button.type = 'button';
    if (focusKey) button.dataset.focusKey = focusKey;
    button.addEventListener('click', action);
    return button;
  }
  function showNotice(message, error = false) {
    clearTimeout(noticeTimer);
    $('notice').textContent = message;
    $('notice').classList.toggle('error', error);
    $('notice').hidden = false;
    noticeTimer = setTimeout(() => { $('notice').hidden = true; }, error ? 11000 : 5500);
  }
  function formError(id, message = '') { $(id).textContent = message; $(id).hidden = !message; }
  function selectedDevice() { return state.devices.find(device => String(device.id) === String(state.selectedId)); }
  async function api(path, options = {}) {
    const headers = {...options.headers};
    if (options.method && options.method !== 'GET') {
      headers['X-FTP-App'] = '1';
      if (state.session?.csrf) headers['X-CSRF-Token'] = state.session.csrf;
    }
    if (options.body !== undefined) { headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(options.body); }
    const {response, data} = await sessionGate.protect((async () => {
      let response;
      try { response = await fetch(path, {...options, headers, credentials: 'same-origin'}); }
      catch { throw new Error('Relay could not reach the host computer. Check the connection and try again.'); }
      const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
      return {response, data};
    })());
    if (!response.ok) {
      if (response.status === 401 && !['/api/login', '/api/setup'].includes(path)) expireSession();
      const error = new Error(data.error || `The request could not be completed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function expireSession() {
    sessionGate.invalidate();
    state.pollGeneration++;
    state.jobsGeneration++;
    state.sendGeneration++;
    clearTimeout(state.polling);
    state.session = null;
    state.sources.clear();
    state.selectedFile = null;
    state.retryTarget = null;
    state.benchmarkId = '';
    $('file-input').value = '';
    state.jobs = [];
    state.devices = [];
    state.selectedId = null;
    state.filesGeneration++;
    for (const request of state.requests.values()) request.abort();
    state.requests.clear();
    state.staging.clear();
    state.sending = false;
    for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
    $('app-shell').hidden = true;
    $('auth-screen').hidden = false;
    configureAuth(false);
  }
  function configureAuth(setup, setupAllowed = true) {
    $('auth-title').textContent = setup ? 'Make yourself at home.' : 'Welcome back.';
    $('auth-description').textContent = setup ? 'Create your local account. Your files have places to be.' : 'Sign in to pick up where your files left off.';
    $('auth-submit').replaceChildren(document.createTextNode(setup ? 'Create workspace' : 'Sign in'), element('span', '', '↗'));
    $('auth-password').autocomplete = setup ? 'new-password' : 'current-password';
    $('auth-password').minLength = setup ? 8 : 1;
    $('auth-password').placeholder = setup ? 'At least 8 characters' : 'Your password';
    $('auth-form').dataset.setup = setup ? 'true' : 'false';
    $('auth-form').hidden = false;
    $('session-retry').hidden = true;
    if (setup && !setupAllowed) {
      $('auth-title').textContent = 'The host goes first.';
      $('auth-description').textContent = 'Create the workspace account on the host computer at http://127.0.0.1' + (location.port ? ':' + location.port : '') + '. Then come back here to sign in.';
      $('auth-form').hidden = true;
      $('session-retry').hidden = false;
    }
  }
  async function loadSession() {
    $('session-retry').hidden = true;
    try {
      state.session = await api('/api/session');
      if (state.session.user) await enterWorkspace();
      else configureAuth(state.session.setup_required, state.session.setup_allowed);
    } catch (error) {
      $('auth-title').textContent = 'A small connection hiccup.';
      $('auth-description').textContent = error.message;
      $('session-retry').hidden = false;
    }
  }
  async function enterWorkspace() {
    sessionGate.invalidate();
    $('auth-screen').hidden = true;
    $('app-shell').hidden = false;
    $('auth-password').value = '';
    $('account-name').textContent = state.session.user.username;
    formError('auth-error');
    updateFileSelection();
    try {
      const [devicesData, jobsData] = await Promise.all([api('/api/devices'), api('/api/jobs')]);
      if (!state.session?.user) return;
      state.devices = devicesData.devices;
      state.jobs = jobsData.jobs;
      state.selectedId = state.devices.find(device => device.ready)?.id ?? state.devices[0]?.id ?? null;
      state.path = '/';
      renderDevices();
      renderJobs();
      if (selectedDevice()?.ready) await loadFiles();
      else renderFilesEmpty('A place for everything.', 'Connect a device to explore its files.');
      startPolling();
    } catch (error) { if (!error.stale) { showNotice(error.message, true); if (state.session?.user) startPolling(); } }
  }
  function openDialog(dialog) {
    dialogOpener = document.activeElement;
    dialog.showModal();
    const first = dialog.querySelector('input, button');
    if (first) first.focus();
  }
  for (const dialog of document.querySelectorAll('dialog')) {
    dialog.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', () => dialog.close()));
    dialog.addEventListener('close', () => { if (dialogOpener?.isConnected) dialogOpener.focus(); });
  }
  function confirmAction(title, description, label, callback) {
    $('confirm-title').textContent = title;
    $('confirm-description').textContent = description;
    $('confirm-action').textContent = label;
    confirmCallback = callback;
    openDialog($('confirm-dialog'));
  }
  $('confirm-action').addEventListener('click', async () => {
    const callback = confirmCallback;
    $('confirm-action').disabled = true;
    try { if (callback) await callback(); $('confirm-dialog').close(); }
    catch (error) { showNotice(error.message, true); }
    finally { $('confirm-action').disabled = false; }
  });
  $('session-retry').addEventListener('click', loadSession);
  $('auth-form').addEventListener('submit', async event => {
    event.preventDefault();
    const submit = $('auth-submit');
    submit.disabled = true;
    formError('auth-error');
    try {
      const endpoint = $('auth-form').dataset.setup === 'true' ? '/api/setup' : '/api/login';
      state.session = await api(endpoint, {method: 'POST', body: {username: $('auth-username').value.trim(), password: $('auth-password').value}});
      await enterWorkspace();
    } catch (error) { formError('auth-error', error.message); }
    finally { submit.disabled = false; }
  });
  $('logout').addEventListener('click', () => {
    const logout = async () => { await api('/api/logout', {method: 'POST'}); expireSession(); $('auth-password').value = ''; $('auth-username').focus(); };
    if (state.jobs.some(active)) confirmAction('Sign out with files in motion?', 'Your active transfers may be interrupted. You can sign in again to check their status.', 'Sign out', logout);
    else logout().catch(error => showNotice(error.message, true));
  });

  async function refreshDevices() {
    state.devices = (await api('/api/devices')).devices;
    if (!selectedDevice()) state.selectedId = state.devices.find(device => device.ready)?.id ?? state.devices[0]?.id ?? null;
    renderDevices();
  }
  function updateControls() {
    const device = selectedDevice();
    const ready = Boolean(device?.ready);
    $('upload-button').disabled = !ready || !state.selectedFile || state.sending;
    $('benchmark-button').disabled = !ready || !state.selectedFile || state.sending;
    $('refresh-files').disabled = !ready;
    $('parent-folder').disabled = !ready || state.path === '/';
    $('connection-dot').classList.toggle('muted', !ready);
    $('connection-label').textContent = device ? `${ready ? 'Connected to' : 'Connect'} ${device.name}` : 'Choose a device';
    $('upload-destination').textContent = device ? `Destination: ${device.name} · ${state.path}${$('compress').checked ? ' · .gz file' : ''}` : 'Choose a connected device to get started.';
    $('current-path').textContent = state.path;
    $('current-path').title = state.path;
  }
  function renderDevices() {
    $('device-count').textContent = state.devices.length;
    $('device-list').replaceChildren();
    if (!state.devices.length) $('device-list').append(element('p', 'aside-description', 'Add your first device to get moving.'));
    for (const device of state.devices) {
      const row = element('div', 'device-row');
      const button = actionButton('', `device-item${String(device.id) === String(state.selectedId) ? ' selected' : ''}`, () => selectDevice(device), `device-${device.id}`);
      button.setAttribute('aria-pressed', String(String(device.id) === String(state.selectedId)));
      const image = element('span', 'device-icon'); image.append(icon('device'));
      const copy = element('span', 'device-copy');
      copy.append(element('strong', '', device.name), element('small', '', device.local ? 'Shared folder on the host' : `${device.host}:${device.port}`));
      const dot = element('span', `status-dot${device.ready ? '' : ' muted'}`); dot.setAttribute('aria-hidden', 'true');
      button.setAttribute('aria-label', `${device.name}, ${device.ready ? 'connected' : 'not connected'}`);
      button.append(image, copy, dot);
      row.append(button);
      if (!device.local) {
        const remove = actionButton('×', 'icon-button device-remove', () => removeDevice(device));
        remove.setAttribute('aria-label', `Remove ${device.name}`); remove.title = `Remove ${device.name}`;
        row.append(remove);
      }
      $('device-list').append(row);
    }
    updateControls();
  }
  async function selectDevice(device) {
    state.selectedId = device.id;
    state.path = '/';
    state.filesGeneration++;
    renderDevices();
    if (device.ready) await loadFiles();
    else {
      renderFilesEmpty('Almost there.', 'Connect this device to see its files and start a transfer.');
      connectDeviceId = device.id;
      $('connect-dialog-title').textContent = `Connect ${device.name}`;
      $('connect-description').textContent = `${device.username} at ${device.host}:${device.port}`;
      $('connect-password').value = '';
      $('connect-password').required = !device.local;
      formError('connect-error');
      openDialog($('connect-dialog'));
      $('connect-password').focus();
    }
  }
  function removeDevice(device) {
    confirmAction(`Remove ${device.name}?`, 'This removes its saved connection details. Files on the device will stay where they are.', 'Remove device', async () => {
      await api(`/api/devices/${encodeURIComponent(device.id)}`, {method: 'DELETE'});
      await refreshDevices();
      state.path = '/';
      await loadFiles();
      showNotice('Device removed.');
    });
  }
  $('add-device').addEventListener('click', () => { $('device-form').reset(); formError('device-error'); openDialog($('device-dialog')); $('device-name').focus(); });
  $('device-form').addEventListener('submit', async event => {
    event.preventDefault();
    const submit = $('device-form').querySelector('[type=submit]');
    submit.disabled = true;
    formError('device-error');
    try {
      const {device} = await api('/api/devices', {method: 'POST', body: {name: $('device-name').value.trim(), host: $('device-host').value.trim(), port: Number($('device-port').value), username: $('device-username').value.trim()}});
      $('device-dialog').close();
      await refreshDevices();
      await selectDevice(state.devices.find(saved => String(saved.id) === String(device.id)) || device);
    } catch (error) { formError('device-error', error.message); }
    finally { submit.disabled = false; }
  });
  $('connect-form').addEventListener('submit', async event => {
    event.preventDefault();
    const submit = $('connect-form').querySelector('[type=submit]');
    submit.disabled = true;
    formError('connect-error');
    try {
      await api(`/api/devices/${encodeURIComponent(connectDeviceId)}/connect`, {method: 'POST', body: {password: $('connect-password').value}});
      $('connect-password').value = '';
      $('connect-dialog').close();
      await refreshDevices();
      await loadFiles();
      showNotice('Connected. Your files are ready.');
    } catch (error) { formError('connect-error', error.message); }
    finally { submit.disabled = false; }
  });
  $('connect-dialog').addEventListener('close', () => { $('connect-password').value = ''; });
  function renderFilesEmpty(title, description) {
    const empty = element('div', 'empty-state');
    const image = element('span', 'empty-icon', '▱'); image.setAttribute('aria-hidden', 'true');
    empty.append(image, element('h3', '', title), element('p', '', description));
    $('remote-files').replaceChildren(empty);
    $('files-count').textContent = 'No files to show';
    updateControls();
  }
  async function loadFiles(path = state.path) {
    const device = selectedDevice();
    const generation = ++state.filesGeneration;
    if (!device?.ready) { renderFilesEmpty('A place for everything.', 'Choose a connected device to explore its files.'); return; }
    renderFilesEmpty('Opening your folder…', 'Just a moment.');
    $('refresh-files').disabled = true;
    try {
      const data = await api(`/api/devices/${encodeURIComponent(device.id)}/files?path=${encodeURIComponent(path)}`);
      if (generation !== state.filesGeneration || !state.session?.user) return;
      state.path = data.path;
      renderFiles(data.entries);
    } catch (error) {
      if (generation === state.filesGeneration) { renderFilesEmpty('Could not open this folder.', error.message); showNotice(error.message, true); }
    } finally { if (generation === state.filesGeneration) updateControls(); }
  }
  function renderFiles(entries) {
    $('remote-files').replaceChildren();
    if (!entries.length) renderFilesEmpty('Room for something new.', 'This folder is empty. Send a file to make yourself at home.');
    const ordered = [...entries].sort((a, b) => Number(b.directory) - Number(a.directory) || a.name.localeCompare(b.name));
    for (const entry of ordered) {
      const row = element('div', 'file-row');
      const image = element('span', 'file-kind'); image.append(icon(entry.directory ? 'folder' : 'file'));
      const copy = element('span', 'file-copy');
      copy.append(element('span', 'file-name', entry.name), element('small', '', entry.directory ? 'Folder' : formatBytes(entry.size)));
      row.append(image);
      if (entry.directory) {
        const open = actionButton('', 'folder-button', () => loadFiles(joinPath(state.path, entry.name)));
        open.setAttribute('aria-label', `Open folder ${entry.name}`);
        open.append(copy); row.append(open, element('span', '', '›'));
      } else {
        const download = actionButton('↓', 'icon-button', () => startDownload(entry));
        download.setAttribute('aria-label', `Download ${entry.name}`); download.title = `Download ${entry.name}`;
        row.append(copy, download);
      }
      $('remote-files').append(row);
    }
    $('files-count').textContent = `${entries.length} ${entries.length === 1 ? 'item' : 'items'}`;
    updateControls();
  }
  $('refresh-files').addEventListener('click', () => loadFiles());
  $('parent-folder').addEventListener('click', () => loadFiles(parentPath(state.path)));

  function chooseFile(file) {
    if (!file) return;
    state.selectedFile = file;
    updateFileSelection();
    if (state.retryTarget) {
      const original = state.retryTarget;
      state.retryTarget = null;
      if (file.name !== original.name || file.size !== original.size) { showNotice(`To retry, choose the original ${original.name} (${formatBytes(original.size)}).`, true); return; }
      sendFile(original.kind, original);
    }
  }
  function updateFileSelection() {
    const file = state.selectedFile;
    $('dropzone').classList.toggle('has-file', Boolean(file));
    $('drop-title').textContent = file ? file.name : 'A new home for your files';
    $('drop-description').textContent = file ? 'All picked out. Ready when you are.' : 'Drop a file here, or choose one below';
    $('selected-file-meta').textContent = file ? `${formatBytes(file.size)} · ${file.type || 'File'}` : 'Audio, video, documents — make it yours.';
    updateControls();
  }
  $('file-input').addEventListener('change', () => { chooseFile($('file-input').files[0]); $('file-input').value = ''; });
  $('file-input').addEventListener('cancel', () => { state.retryTarget = null; });
  $('dropzone').addEventListener('dragover', event => { event.preventDefault(); $('dropzone').classList.add('drag-over'); });
  $('dropzone').addEventListener('dragleave', event => { if (!$('dropzone').contains(event.relatedTarget)) $('dropzone').classList.remove('drag-over'); });
  $('dropzone').addEventListener('drop', event => {
    event.preventDefault(); $('dropzone').classList.remove('drag-over');
    const files = event.dataTransfer.files;
    if (files.length > 1) { showNotice('Choose one file at a time. You can send another once this file has been staged.'); return; }
    chooseFile(files[0]);
  });
  // Prevent a dropped file outside the drop area from navigating away.
  document.addEventListener('dragover', event => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); });
  document.addEventListener('drop', event => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); });
  $('compress').addEventListener('change', updateControls);
  $('upload-button').addEventListener('click', () => sendFile('upload'));
  $('benchmark-button').addEventListener('click', () => sendFile('benchmark'));

  function upsertJob(job) {
    state.jobsGeneration++;
    const index = state.jobs.findIndex(item => String(item.id) === String(job.id));
    if (index >= 0) state.jobs[index] = job; else state.jobs.unshift(job);
  }
  function streamSource(job, file) {
    return sessionGate.protect(new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const id = String(job.id);
      state.requests.set(id, xhr);
      state.staging.set(id, {done: 0, total: file.size, started: performance.now(), speed: 0});
      xhr.open('PUT', `/api/jobs/${encodeURIComponent(job.id)}/source`);
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.setRequestHeader('X-FTP-App', '1');
      xhr.setRequestHeader('X-CSRF-Token', state.session.csrf);
      xhr.withCredentials = true;
      xhr.upload.addEventListener('progress', event => {
        const stage = state.staging.get(id);
        if (!stage) return;
        stage.done = event.loaded;
        stage.total = event.lengthComputable ? event.total : file.size;
        stage.speed = event.loaded / Math.max((performance.now() - stage.started) / 1000, .001);
        renderJobs();
      });
      const cleanup = () => { state.requests.delete(id); state.staging.delete(id); };
      xhr.addEventListener('load', () => {
        cleanup();
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch { /* server errors may lack JSON */ }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data.job);
        else { if (xhr.status === 401) expireSession(); reject(new Error(data.error || `The file could not be staged (${xhr.status}).`)); }
      });
      xhr.addEventListener('error', () => { cleanup(); reject(new Error('The connection was interrupted while preparing your file. You can retry from its transfer card.')); });
      xhr.addEventListener('abort', () => { cleanup(); const error = new Error('Transfer cancelled.'); error.cancelled = true; reject(error); });
      xhr.send(file);
      renderJobs();
    }));
  }
  async function sendFile(kind, original = null, sourceFile = null) {
    const device = original ? state.devices.find(item => String(item.id) === String(original.device_id)) : selectedDevice();
    const file = sourceFile || state.selectedFile;
    if (!file || state.sending) return;
    if (!device?.ready) { showNotice('Connect this file’s destination device before retrying.', true); return; }
    const generation = ++state.sendGeneration;
    state.sending = true; updateControls();
    let created;
    try {
      const request = original ? {device_id: original.device_id, kind: original.kind, name: file.name, size: file.size, remote_path: original.remote_path, compress: original.compress, unpack: original.unpack} : {device_id: device.id, kind, name: file.name, size: file.size, remote_path: state.path, compress: kind === 'upload' && $('compress').checked, unpack: false};
      created = (await api('/api/jobs', {method: 'POST', body: request})).job;
      state.sources.set(String(created.id), file);
      upsertJob(created);
      if (kind === 'benchmark') { state.benchmarkId = String(created.id); switchView('performance'); }
      renderJobs(); startPolling();
      const job = await streamSource(created, file);
      if (job && state.session?.user) upsertJob(job);
      showNotice(kind === 'benchmark' ? 'File prepared. Comparing both transfer methods.' : 'File prepared. Sending to your device.');
      await refreshJobs();
    } catch (error) {
      if (!error.cancelled && !error.stale) showNotice(error.message, true);
      if (created && !error.stale && state.session?.user) {
        try {
          const current = (await api(`/api/jobs/${encodeURIComponent(created.id)}`)).job;
          // Failed staging can leave a job waiting for a source; cancel it so retry is available.
          if (current.status === 'awaiting_upload') await api(`/api/jobs/${encodeURIComponent(created.id)}/cancel`, {method: 'POST'});
          await refreshJobs();
        } catch { /* polling will recover the server state */ }
      }
    } finally { if (generation === state.sendGeneration) { state.sending = false; updateControls(); renderJobs(); } }
  }
  async function startDownload(entry) {
    const device = selectedDevice();
    if (!device?.ready) return;
    try {
      const {job} = await api('/api/jobs', {method: 'POST', body: {device_id: device.id, kind: 'download', name: entry.name, size: entry.size, remote_path: joinPath(state.path, entry.name), compress: false, unpack: $('unpack').checked && entry.name.toLowerCase().endsWith('.gz')}});
      upsertJob(job); renderJobs(); startPolling();
      showNotice('Getting your file from the device. Save it when it is ready.');
    } catch (error) { showNotice(error.message, true); }
  }
  async function cancelJob(job) {
    try {
      await api(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, {method: 'POST'});
      state.requests.get(String(job.id))?.abort();
      await refreshJobs();
      showNotice('Cancellation requested.');
    } catch (error) { showNotice(error.message, true); }
  }
  async function retryJob(job) {
    if (job.kind === 'download') {
      try {
        const {job: next} = await api('/api/jobs', {method: 'POST', body: {device_id: job.device_id, kind: 'download', name: job.name, size: job.size, remote_path: job.remote_path, compress: job.compress, unpack: job.unpack}});
        upsertJob(next); renderJobs(); startPolling(); switchView('transfers');
      } catch (error) { showNotice(error.message, true); }
      return;
    }
    const file = state.sources.get(String(job.id));
    if (file) { await sendFile(job.kind, job, file); return; }
    state.retryTarget = job;
    showNotice(`Choose the original ${job.name} to retry.`);
    $('file-input').click();
  }
  async function deleteJob(job) {
    confirmAction('Remove this history item?', 'This removes the record and its local download or report. Files on your FTP device stay where they are.', 'Remove record', async () => {
      await api(`/api/jobs/${encodeURIComponent(job.id)}`, {method: 'DELETE'});
      state.sources.delete(String(job.id));
      await refreshJobs();
    });
  }
  function attachment(job, suffix, label) {
    const link = element('a', 'button secondary', label);
    link.href = `/api/jobs/${encodeURIComponent(job.id)}/${suffix}`;
    link.setAttribute('download', suffix === 'download' ? downloadPresentation(job).name : '');
    if (suffix === 'download') link.title = `Save ${downloadPresentation(job).name}`;
    link.dataset.focusKey = `${suffix}-${job.id}`;
    return link;
  }
  function jobCard(job, history = false) {
    const stage = state.staging.get(String(job.id));
    const isActive = active(job);
    const card = element('article', 'job-card');
    const heading = element('div', 'job-top');
    const symbol = element('span', 'job-icon', job.kind === 'download' ? '↓' : job.kind === 'benchmark' ? '↔' : '↑'); symbol.setAttribute('aria-hidden', 'true');
    const title = element('div', 'job-title');
    const presentation = downloadPresentation(job);
    const date = new Date(job.created);
    const dateLabel = Number.isNaN(date.getTime()) ? '' : date.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
    title.append(element('h3', '', presentation.name), element('p', '', `${job.kind === 'benchmark' ? 'Compression test' : job.kind === 'download' ? 'Download' : 'Upload'} · ${job.device_name || 'Device'}${history && dateLabel ? ` · ${dateLabel}` : ''}`));
    if (presentation.detail) title.append(element('p', '', presentation.detail));
    const statusNames = {awaiting_upload: 'Preparing', queued: 'Queued', running: 'In progress', completed: 'Completed', failed: 'Interrupted', cancelled: 'Cancelled'};
    heading.append(symbol, title, element('span', `job-status ${job.status}`, stage ? 'Preparing' : statusNames[job.status] || job.status)); card.append(heading);
    const done = stage ? stage.done : Number(job.done) || 0;
    const total = stage ? stage.total : Number(job.total) || 0;
    const percent = stage ? (total ? done / total * 100 : 0) : finite(job.percent) ? job.percent : total ? done / total * 100 : 0;
    const speed = stage ? stage.speed : job.speed_bps;
    const eta = stage ? (speed > 0 ? (total - done) / speed : null) : job.eta_seconds;
    if (isActive || job.status === 'completed') {
      const progress = element('div', 'progress-track');
      progress.setAttribute('role', 'progressbar'); progress.setAttribute('aria-label', `${job.name}: ${stage ? 'preparing file' : job.phase || 'transfer'}`);
      progress.setAttribute('aria-valuemin', '0'); progress.setAttribute('aria-valuemax', '100');
      progress.setAttribute('aria-valuenow', String(job.status === 'completed' ? 100 : Math.max(0, Math.min(100, Math.round(percent)))));
      const fill = element('span', `progress-fill${stage ? ' staging' : ''}`); fill.style.width = `${job.status === 'completed' ? 100 : Math.max(0, Math.min(100, percent))}%`; progress.append(fill); card.append(progress);
    }
    const metrics = element('div', 'job-metrics');
    metrics.append(element('span', '', stage ? 'Preparing file for transfer' : job.phase || statusNames[job.status] || job.status));
    metrics.append(element('span', '', `${formatBytes(done)} / ${formatBytes(total)}`));
    if (isActive) {
      metrics.append(element('strong', '', finite(speed) && speed > 0 ? `${formatBytes(speed)}/s` : 'Measuring speed…'));
      metrics.append(element('span', '', finite(eta) ? `${formatDuration(eta)} remaining` : 'Estimating time…'));
    } else if (finite(job.elapsed_seconds)) metrics.append(element('span', '', `${formatDuration(job.elapsed_seconds)} elapsed`));
    card.append(metrics);
    if (job.error) card.append(element('p', 'job-error', job.error));
    const buttons = element('div', 'job-buttons');
    if (isActive) buttons.append(actionButton('Cancel', 'button text', () => cancelJob(job), `cancel-${job.id}`));
    if (job.status === 'completed' && job.kind === 'download') buttons.append(attachment(job, 'download', presentation.saveLabel));
    if (job.status === 'completed' && job.kind === 'benchmark') buttons.append(actionButton('View results ↗', 'button secondary', () => { state.benchmarkId = String(job.id); switchView('performance'); renderPerformance(); }, `results-${job.id}`));
    if (job.status === 'failed' || job.status === 'cancelled') buttons.append(actionButton('Retry ↗', 'button secondary', () => retryJob(job), `retry-${job.id}`));
    if (history) buttons.append(actionButton('Remove record', 'button text', () => deleteJob(job), `remove-${job.id}`));
    if (buttons.childElementCount) card.append(buttons);
    return card;
  }
  function renderJobs() {
    if (!state.session?.user) return;
    const focusKey = document.activeElement?.dataset.focusKey;
    const activeJobs = state.jobs.filter(active);
    const finishedJobs = state.jobs.filter(job => !active(job));
    $('active-count').textContent = activeJobs.length;
    $('history-count').textContent = finishedJobs.length;
    $('active-jobs').replaceChildren();
    // Keep a few recent results visible so a finished download's Save button stays easy to find.
    const current = [...activeJobs, ...finishedJobs.slice(0, 2)];
    if (!current.length) $('active-jobs').append(element('div', 'job-empty', 'Nothing in motion just yet. Your next file is a good place to start.'));
    for (const job of current) $('active-jobs').append(jobCard(job));
    $('history-jobs').replaceChildren();
    if (!finishedJobs.length) $('history-jobs').append(element('div', 'job-empty', 'A fresh start. Your finished transfers will appear here.'));
    for (const job of finishedJobs) $('history-jobs').append(jobCard(job, true));
    if (state.view === 'performance') renderPerformance();
    if (focusKey) {
      for (const button of document.querySelectorAll('[data-focus-key]')) if (button.dataset.focusKey === focusKey && button.closest('[hidden]') === null) { button.focus({preventScroll: true}); break; }
    }
  }
  async function refreshJobs() {
    const generation = ++state.jobsGeneration;
    const previous = new Map(state.jobs.map(job => [String(job.id), job]));
    const data = await api('/api/jobs');
    if (!state.session?.user || generation !== state.jobsGeneration) return;
    state.jobs = data.jobs;
    const retryableSources = new Set(state.jobs.filter(job => job.status !== 'completed').map(job => String(job.id)));
    for (const id of state.sources.keys()) if (!retryableSources.has(id)) state.sources.delete(id);
    let refreshDirectory = false;
    for (const job of state.jobs) {
      const old = previous.get(String(job.id));
      if (old && active(old) && terminal.has(job.status)) {
        if (job.status === 'completed') {
          showNotice(job.kind === 'download' ? `${job.name} is ready. Select Save file to download it.` : job.kind === 'benchmark' ? `${job.name}: comparison complete.` : `${job.name} arrived at its destination.`);
          if (job.kind === 'upload' && String(job.device_id) === String(state.selectedId)) refreshDirectory = true;
        } else if (job.status === 'failed') showNotice(`${job.name}: ${job.error || 'Transfer interrupted. You can retry.'}`, true);
      }
    }
    renderJobs();
    if (refreshDirectory) await loadFiles();
  }
  function startPolling() {
    const generation = ++state.pollGeneration;
    clearTimeout(state.polling);
    if (!state.session?.user) return;
    state.polling = setTimeout(async () => {
      try { await refreshJobs(); }
      catch (error) { if (!error.stale && state.session?.user && generation === state.pollGeneration) showNotice(error.message, true); }
      finally { if (generation === state.pollGeneration) startPolling(); }
    }, state.jobs.some(active) || state.sending ? 1000 : 8000);
  }
  $('refresh-history').addEventListener('click', () => refreshJobs().catch(error => showNotice(error.message, true)));
  function switchView(view) {
    state.view = view;
    for (const button of document.querySelectorAll('[data-view]')) {
      const chosen = button.dataset.view === view;
      button.classList.toggle('active', chosen);
      if (chosen) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
    }
    for (const name of ['transfers', 'history', 'performance', 'protocol']) $(`view-${name}`).hidden = name !== view;
    if (view === 'performance') renderPerformance();
    if (view === 'protocol') renderProtocol();
  }
  for (const button of document.querySelectorAll('[data-view]')) button.addEventListener('click', () => switchView(button.dataset.view));

  function svgNode(tag, attrs = {}, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function chartFrame(label) {
    const svg = svgNode('svg', {viewBox: '0 0 400 230', role: 'img', 'aria-label': label});
    svg.append(svgNode('title', {}, label));
    return svg;
  }
  function chartLegend() {
    const legend = element('div', 'chart-legend');
    for (const [mode, name] of [['plain', 'Original'], ['gzip', 'Gzip']]) { const label = element('span'); label.append(element('i', `legend-dot ${mode}`), document.createTextNode(name)); legend.append(label); }
    return legend;
  }
  function throughputChart(job) {
    const card = element('section', 'card chart-card');
    card.append(element('h3', '', 'Transfer speed over time'), element('p', '', 'FTP payload throughput · MiB/s'));
    const series = throughputSeries(job.samples);
    const all = [...series.plain, ...series.gzip];
    if (!all.length) { card.append(element('p', 'job-empty', 'Speed samples will appear as the test runs.')); return card; }
    const svg = chartFrame('Actual FTP throughput over elapsed seconds, comparing original and gzip transfers');
    const maxX = Math.max(1, ...all.map(point => point.x));
    const maxY = Math.max(.01, ...all.map(point => point.y)) * 1.1;
    const left = 44, top = 17, width = 337, height = 166;
    for (let step = 0; step <= 4; step++) {
      const y = top + height * step / 4;
      svg.append(svgNode('line', {x1: left, y1: y, x2: left + width, y2: y, stroke: '#eaf0e4', 'stroke-width': 1}));
      svg.append(svgNode('text', {x: left - 9, y: y + 3, 'text-anchor': 'end'}, Number((maxY * (1 - step / 4)).toFixed(2))));
    }
    for (let step = 0; step <= 4; step++) svg.append(svgNode('text', {x: left + width * step / 4, y: top + height + 20, 'text-anchor': 'middle'}, `${Number((maxX * step / 4).toFixed(1))}s`));
    for (const [mode, values] of Object.entries(series)) {
      if (!values.length) continue;
      const color = mode === 'plain' ? '#237562' : '#b5ca91';
      const points = values.map(point => `${left + point.x / maxX * width},${top + height - point.y / maxY * height}`).join(' ');
      svg.append(svgNode('polyline', {points, fill: 'none', stroke: color, 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}));
      if (values.length === 1) svg.append(svgNode('circle', {cx: left + values[0].x / maxX * width, cy: top + height - values[0].y / maxY * height, r: 3.5, fill: color}));
    }
    card.append(svg, chartLegend());
    return card;
  }
  function payloadChart(runs) {
    const card = element('section', 'card chart-card');
    card.append(element('h3', '', 'A smaller package?'), element('p', '', 'Actual bytes sent · original vs gzip'));
    const svg = chartFrame('Comparison of actual original and gzip payload file sizes');
    const maxBytes = Math.max(1, ...runs.map(run => Number(run.payload_bytes) || 0));
    for (let index = 0; index < runs.length; index++) {
      const run = runs[index];
      const y = 45 + index * 85;
      const width = Math.max(0, Number(run.payload_bytes) || 0) / maxBytes * 270;
      svg.append(svgNode('text', {x: 10, y: y - 12}, run.mode === 'plain' ? 'Original file' : 'Gzip file'));
      svg.append(svgNode('rect', {x: 10, y, width: 300, height: 24, rx: 5, fill: '#f0f4eb'}));
      svg.append(svgNode('rect', {x: 10, y, width, height: 24, rx: 5, fill: run.mode === 'plain' ? '#237562' : '#b5ca91'}));
      svg.append(svgNode('text', {x: 10, y: y + 43}, formatBytes(run.payload_bytes)));
    }
    card.append(svg, chartLegend());
    return card;
  }
  function metric(label, value, detail) {
    const card = element('div', 'metric-card');
    card.append(element('div', 'metric-label', label), element('div', 'metric-value', value), element('div', 'metric-detail', detail));
    return card;
  }
  function renderPerformance() {
    const benchmarks = state.jobs.filter(job => job.kind === 'benchmark');
    const selector = $('benchmark-select');
    const selection = benchmarks.find(job => String(job.id) === state.benchmarkId) || benchmarks.find(job => job.status === 'completed') || benchmarks[0];
    state.benchmarkId = selection ? String(selection.id) : '';
    const optionsKey = JSON.stringify(benchmarks.map(job => [job.id, job.name, job.status]));
    if (selector.dataset.optionsKey !== optionsKey) {
      selector.replaceChildren();
      if (!benchmarks.length) selector.append(element('option', '', 'No tests yet'));
      for (const job of benchmarks) {
        const option = element('option', '', `${job.name} · ${job.status === 'completed' ? 'Complete' : job.status}`);
        option.value = String(job.id); selector.append(option);
      }
      selector.dataset.optionsKey = optionsKey;
    }
    if (selection) selector.value = state.benchmarkId;
    selector.disabled = !benchmarks.length;
    $('performance-results').replaceChildren();
    if (!selection) { $('performance-results').append(element('div', 'job-empty', 'A little curiosity goes a long way. Run your first comparison to see real results.')); return; }
    if (selection.status !== 'completed') {
      $('performance-results').append(jobCard(selection));
      if (selection.samples?.length) $('performance-results').append(throughputChart(selection));
      return;
    }
    const runs = selection.result?.runs || [];
    const measured = comparisonMetrics(selection.result);
    const metrics = element('div', 'metric-grid');
    metrics.append(metric('FILE SIZE CHANGE', measured.reduction === null ? '—' : `${Math.abs(measured.reduction)}%`, measured.reduction === null ? 'Not measured' : measured.reduction >= 0 ? 'Smaller with gzip' : 'Larger with gzip'));
    metrics.append(metric('END-TO-END TIME', measured.timeSaved === null ? '—' : `${Math.abs(measured.timeSaved).toFixed(2)}s`, measured.timeSaved === null ? 'Not measured' : measured.timeSaved >= 0 ? 'Saved with gzip, including preparation' : 'Longer with gzip, including preparation'));
    metrics.append(metric('CONTENT CHECK', measured.verified ? 'Verified' : 'Check failed', measured.verified ? 'Both downloaded files match their SHA-256' : 'One or more files did not verify'));
    const charts = element('div', 'chart-grid'); charts.append(throughputChart(selection), payloadChart(runs));
    const summary = element('div', 'benchmark-summary');
    const table = element('table'); const caption = element('caption', 'visually-hidden', 'Measured benchmark results'); table.append(caption);
    const head = element('thead'); const headRow = element('tr');
    for (const name of ['Method', 'Payload', 'Preparation', 'FTP time', 'Total', 'FTP speed', 'Effective speed', 'Verified']) { const cell = element('th', '', name); cell.scope = 'col'; headRow.append(cell); }
    head.append(headRow); table.append(head);
    const body = element('tbody');
    for (const run of runs) {
      const row = element('tr');
      const values = benchmarkRowValues(run);
      for (const value of values) row.append(element('td', '', value));
      body.append(row);
    }
    table.append(body); summary.append(table);
    const actions = element('div', 'report-actions'); actions.append(element('p', '', 'Preparation includes connection setup and gzip work. Effective speed uses original bytes and total time.'), attachment(selection, 'report.csv', 'Download results .csv ↓')); summary.append(actions);
    $('performance-results').append(metrics, charts, summary);
  }
  $('benchmark-select').addEventListener('change', () => { state.benchmarkId = $('benchmark-select').value; renderPerformance(); });

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  function renderProtocol() {
    renderProtoSubnav();
    renderProtoCatalog();
    renderActiveProtoTarget();
  }

  function renderProtoSubnav() {
    for (const pill of document.querySelectorAll('.subnav-pill')) {
      const match = pill.dataset.protoCat === state.protoCategory;
      pill.classList.toggle('active', match);
      if (match) pill.setAttribute('aria-selected', 'true');
      else pill.removeAttribute('aria-selected');
    }
  }

  function getProtoItems() {
    if (state.protoCategory === 'scenarios') {
      const allScenarios = Object.values(GUIDED_SCENARIOS);
      if (!state.protoSearch) return allScenarios;
      const q = state.protoSearch.toLowerCase();
      return allScenarios.filter(s => s.title.toLowerCase().includes(q) || s.subtitle.toLowerCase().includes(q) || s.description.toLowerCase().includes(q));
    }
    const allCommands = Object.values(commandCatalog);
    let items = allCommands;
    if (state.protoCategory && state.protoCategory !== 'all') {
      items = items.filter(c => c.category === state.protoCategory);
    }
    if (state.protoSearch) {
      const q = state.protoSearch.toLowerCase();
      items = items.filter(c => c.verb.toLowerCase().includes(q) || c.name.toLowerCase().includes(q) || c.description.toLowerCase().includes(q) || c.rfc.toLowerCase().includes(q));
    }
    return items;
  }

  function renderProtoCatalog() {
    const list = $('proto-catalog-list');
    if (!list) return;
    list.replaceChildren();
    const items = getProtoItems();
    if ($('proto-count-badge')) {
      $('proto-count-badge').textContent = state.protoCategory === 'scenarios' ? `${items.length} flow${items.length === 1 ? '' : 's'}` : `${items.length} command${items.length === 1 ? '' : 's'}`;
    }

    if (!items.length) {
      const empty = element('div', 'job-empty', 'No commands or scenarios match your search.');
      list.append(empty);
      return;
    }

    for (const item of items) {
      const btn = element('button', 'proto-cmd-item');
      btn.type = 'button';
      const isScenario = state.protoCategory === 'scenarios';
      const isSelected = isScenario ? item.id === state.protoScenario : item.verb === state.protoCommand;
      if (isSelected) btn.classList.add('active');

      const header = element('div', 'cmd-item-header');
      header.append(
        element('span', 'cmd-item-verb', isScenario ? item.title : item.verb),
        element('span', 'cmd-item-cat', isScenario ? 'SCENARIO' : item.category.toUpperCase())
      );
      btn.append(
        header,
        element('span', 'cmd-item-name', isScenario ? item.subtitle : item.name),
        element('span', 'cmd-item-desc', item.description)
      );

      btn.addEventListener('click', () => {
        if (isScenario) {
          state.protoScenario = item.id;
        } else {
          state.protoCommand = item.verb;
        }
        renderProtoCatalog();
        renderActiveProtoTarget();
      });

      list.append(btn);
    }
  }

  function renderActiveProtoTarget() {
    const isScenario = state.protoCategory === 'scenarios';
    if (isScenario) {
      const scenario = GUIDED_SCENARIOS[state.protoScenario] || GUIDED_SCENARIOS.auth_flow;
      if ($('proto-active-verb')) $('proto-active-verb').textContent = 'FLOW';
      if ($('proto-active-rfc')) $('proto-active-rfc').textContent = 'RFC 959 Guided Scenario';
      if ($('proto-active-channel')) $('proto-active-channel').textContent = 'Multi-Command Workflow';
      if ($('proto-active-title')) $('proto-active-title').textContent = scenario.title;
      if ($('proto-active-desc')) $('proto-active-desc').textContent = scenario.description;
      if ($('proto-arg-container')) $('proto-arg-container').hidden = true;

      const repliesList = $('proto-replies-list');
      if (repliesList) {
        repliesList.replaceChildren();
        for (const step of scenario.steps) {
          if (step.sender === 'client') {
            repliesList.append(element('span', 'code-pill code-intermediate', `C> ${step.text}`));
          } else if (step.sender === 'server') {
            repliesList.append(element('span', 'code-pill code-success', `S> ${step.text.split('\r\n')[0]}`));
          }
        }
      }
    } else {
      const cmd = commandCatalog[state.protoCommand] || commandCatalog.USER;
      if ($('proto-active-verb')) $('proto-active-verb').textContent = cmd.verb;
      if ($('proto-active-rfc')) $('proto-active-rfc').textContent = cmd.rfc;
      if ($('proto-active-channel')) $('proto-active-channel').textContent = cmd.channel === 'control' ? 'Control Connection' : 'Data Connection';
      if ($('proto-active-title')) $('proto-active-title').textContent = `${cmd.verb} — ${cmd.name}`;
      if ($('proto-active-desc')) $('proto-active-desc').textContent = cmd.description;

      if ($('proto-arg-container')) {
        if (cmd.hasArg) {
          $('proto-arg-container').hidden = false;
          if ($('proto-arg-input')) $('proto-arg-input').value = cmd.defaultArg || '';
          if ($('proto-syntax-hint')) $('proto-syntax-hint').textContent = `Syntax: ${cmd.syntax}`;
        } else {
          $('proto-arg-container').hidden = true;
        }
      }

      const repliesList = $('proto-replies-list');
      if (repliesList) {
        repliesList.replaceChildren();
        for (const reply of cmd.replies || []) {
          const code = parseInt(reply, 10);
          let pillClass = 'code-pill';
          if (code >= 200 && code < 300) pillClass += ' code-success';
          else if (code >= 100 && code < 400) pillClass += ' code-intermediate';
          else if (code >= 400) pillClass += ' code-error';
          repliesList.append(element('span', pillClass, reply));
        }
      }
    }
  }

  function appendTerminal(html) {
    const term = $('proto-terminal-body');
    if (!term) return;
    const line = document.createElement('div');
    line.innerHTML = html;
    term.append(line);
    term.scrollTop = term.scrollHeight;
  }

  async function animatePacket(step) {
    const clientNode = $('host-client');
    const serverNode = $('host-server');
    const clientState = $('client-state-pill');
    const serverState = $('server-state-pill');
    const controlPacket = $('control-packet');
    const packetText = $('packet-text');
    const dataStream = $('data-stream');
    const dataLine = $('data-line');
    const dataPipeStatus = $('data-pipe-status');
    const serverDataPort = $('server-data-port');
    const clientDataPort = $('client-data-port');

    if (step.dataPort && serverDataPort) serverDataPort.textContent = `Data: :${step.dataPort}`;
    if (step.clientPort && clientDataPort) clientDataPort.textContent = `Data: :${step.clientPort}`;

    if (step.channel === 'control') {
      if (step.sender === 'client') {
        if (clientNode) clientNode.classList.add('active-pulse');
        if (clientState) {
          clientState.textContent = 'SENDING';
          clientState.classList.add('active');
        }
        if (packetText) packetText.textContent = `C> ${step.text}`;
        if (controlPacket) {
          controlPacket.classList.remove('reply-token');
          controlPacket.style.transition = 'none';
          controlPacket.style.left = '5%';
          controlPacket.hidden = false;
          void controlPacket.offsetWidth;
          controlPacket.style.transition = 'left 0.6s cubic-bezier(0.4, 0, 0.2, 1)';
          controlPacket.style.left = '95%';
        }
        appendTerminal(`<span class="term-cli">C&gt; ${escapeHtml(step.text)}</span>`);
        await new Promise(r => setTimeout(r, 650));
        if (clientNode) clientNode.classList.remove('active-pulse');
      } else if (step.sender === 'server') {
        if (serverNode) serverNode.classList.add('active-pulse');
        if (serverState) {
          serverState.textContent = 'REPLYING';
          serverState.classList.add('active');
        }
        const firstLine = step.text.split('\r\n')[0];
        if (packetText) packetText.textContent = `S> ${firstLine}`;
        if (controlPacket) {
          controlPacket.classList.add('reply-token');
          controlPacket.style.transition = 'none';
          controlPacket.style.left = '95%';
          controlPacket.hidden = false;
          void controlPacket.offsetWidth;
          controlPacket.style.transition = 'left 0.6s cubic-bezier(0.4, 0, 0.2, 1)';
          controlPacket.style.left = '5%';
        }
        appendTerminal(`<span class="term-srv">S&gt; ${escapeHtml(step.text)}</span>`);
        await new Promise(r => setTimeout(r, 650));
        if (serverNode) serverNode.classList.remove('active-pulse');
      }
    } else if (step.channel === 'data') {
      if (dataLine) dataLine.classList.add('active-stream');
      if (dataStream) dataStream.hidden = false;
      const isUpload = step.dir === 'upload';
      if (isUpload) {
        if (clientNode) clientNode.classList.add('active-pulse');
        if (clientState) clientState.textContent = 'STREAMING';
        if (serverState) serverState.textContent = 'RECEIVING';
        if (dataPipeStatus) dataPipeStatus.textContent = `TRANSMITTING (${formatBytes(step.bytes || 0)})`;
      } else {
        if (serverNode) serverNode.classList.add('active-pulse');
        if (serverState) serverState.textContent = 'STREAMING';
        if (clientState) clientState.textContent = 'RECEIVING';
        if (dataPipeStatus) dataPipeStatus.textContent = `RECEIVING (${formatBytes(step.bytes || 0)})`;
      }
      appendTerminal(`<span class="term-dat">[DATA] ${escapeHtml(step.text)}</span>`);
      await new Promise(r => setTimeout(r, 900));
      if (clientNode) clientNode.classList.remove('active-pulse');
      if (serverNode) serverNode.classList.remove('active-pulse');
      if (dataStream) dataStream.hidden = true;
      if (dataLine) dataLine.classList.remove('active-stream');
      if (dataPipeStatus) dataPipeStatus.textContent = 'IDLE (Closed)';
    }
  }

  async function runProtocolFlow() {
    if (state.protoRunning) return;
    state.protoRunning = true;
    const runBtn = $('proto-run-btn');
    if (runBtn) {
      runBtn.disabled = true;
      runBtn.textContent = 'Running…';
    }

    try {
      let steps = [];
      const isScenario = state.protoCategory === 'scenarios';
      if (isScenario) {
        const scenario = GUIDED_SCENARIOS[state.protoScenario] || GUIDED_SCENARIOS.auth_flow;
        steps = scenario.steps;
        appendTerminal(`<span class="term-dim">--- Running Scenario: ${escapeHtml(scenario.title)} ---</span>`);
      } else {
        const cmd = commandCatalog[state.protoCommand] || commandCatalog.USER;
        const arg = $('proto-arg-input') ? $('proto-arg-input').value.trim() : '';
        steps = cmd.run(arg);
        appendTerminal(`<span class="term-dim">--- Executing: ${escapeHtml(cmd.verb)} (${escapeHtml(cmd.name)}) ---</span>`);
      }

      for (const step of steps) {
        if (!state.protoRunning) break;
        await animatePacket(step);
        await new Promise(r => setTimeout(r, 200));
      }
      appendTerminal(`<span class="term-dim"># Command completed. Ready.</span>`);
    } finally {
      if ($('client-state-pill')) {
        $('client-state-pill').textContent = 'IDLE';
        $('client-state-pill').classList.remove('active');
      }
      if ($('server-state-pill')) {
        $('server-state-pill').textContent = 'LISTENING';
        $('server-state-pill').classList.remove('active');
      }
      if ($('control-packet')) $('control-packet').hidden = true;
      state.protoRunning = false;
      if (runBtn) {
        runBtn.disabled = false;
        runBtn.innerHTML = 'Run &amp; Animate <span aria-hidden="true">▶</span>';
      }
    }
  }

  function resetProtocol() {
    state.protoRunning = false;
    if ($('client-state-pill')) {
      $('client-state-pill').textContent = 'IDLE';
      $('client-state-pill').classList.remove('active');
    }
    if ($('server-state-pill')) {
      $('server-state-pill').textContent = 'LISTENING';
      $('server-state-pill').classList.remove('active');
    }
    if ($('client-data-port')) $('client-data-port').textContent = 'Data: Dynamic';
    if ($('server-data-port')) $('server-data-port').textContent = 'Data: :0 (Dynamic)';
    if ($('control-packet')) $('control-packet').hidden = true;
    if ($('data-stream')) $('data-stream').hidden = true;
    if ($('data-line')) $('data-line').classList.remove('active-stream');
    if ($('data-pipe-status')) $('data-pipe-status').textContent = 'IDLE (Closed)';
    const runBtn = $('proto-run-btn');
    if (runBtn) {
      runBtn.disabled = false;
      runBtn.innerHTML = 'Run &amp; Animate <span aria-hidden="true">▶</span>';
    }
    const term = $('proto-terminal-body');
    if (term) {
      term.innerHTML = '<span class="term-dim"># Connecting to FTP control listener at 127.0.0.1:2121...</span>\n<span class="term-srv">S&gt; 220 Python socket FTP lab ready</span>\n<span class="term-hint"># Select any command or scenario and click "Run &amp; Animate".</span>';
    }
  }

  function initProtocolExplorer() {
    for (const pill of document.querySelectorAll('.subnav-pill')) {
      pill.addEventListener('click', () => {
        state.protoCategory = pill.dataset.protoCat;
        renderProtoSubnav();
        renderProtoCatalog();
        renderActiveProtoTarget();
      });
    }

    const searchInput = $('proto-search');
    if (searchInput) {
      searchInput.addEventListener('input', () => {
        state.protoSearch = searchInput.value.trim();
        renderProtoCatalog();
      });
    }

    const runBtn = $('proto-run-btn');
    if (runBtn) runBtn.addEventListener('click', runProtocolFlow);

    const resetBtn = $('proto-reset-btn');
    if (resetBtn) resetBtn.addEventListener('click', resetProtocol);

    const clearTermBtn = $('proto-clear-term');
    if (clearTermBtn) {
      clearTermBtn.addEventListener('click', () => {
        const term = $('proto-terminal-body');
        if (term) term.replaceChildren();
      });
    }

    renderProtocol();
  }

  initProtocolExplorer();
  loadSession();
})();
