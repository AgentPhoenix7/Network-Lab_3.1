const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const source = path.join(__dirname, "../ftp_lab/webapp/static/app.js");
const ui = fs.existsSync(source) ? require(source) : {};

test('completed unpacked downloads show the saved filename and retain the source name separately', () => {
  assert.equal(typeof ui.downloadPresentation, 'function');
  assert.deepEqual(ui.downloadPresentation({kind: 'download', status: 'completed',
    name: 'lecture.tar.gz', unpack: true, result: {filename: 'lecture.tar', bytes: 1234}}), {
    name: 'lecture.tar', detail: 'Unpacked from lecture.tar.gz · 1.2 KiB ready to save',
    saveLabel: 'Save unpacked file ↓',
  });
});

test('pending, original, and non-download jobs keep their original filename', () => {
  assert.equal(typeof ui.downloadPresentation, 'function');
  for (const job of [
    {kind: 'download', status: 'running', name: 'clip.wav.gz', unpack: true, result: {}},
    {kind: 'download', status: 'completed', name: 'clip.wav.gz', unpack: false, result: {filename: 'clip.wav.gz'}},
    {kind: 'upload', status: 'completed', name: 'clip.wav.gz', unpack: false, result: {}},
  ]) {
    assert.deepEqual(ui.downloadPresentation(job), {name: 'clip.wav.gz', detail: '', saveLabel: 'Save file ↓'});
  }
});

test("file sizes and remaining times stay readable at boundary values", () => {
  assert.equal(
    typeof ui.formatBytes,
    "function",
    "frontend formatting is not implemented",
  );
  assert.equal(ui.formatBytes(0), "0 B");
  assert.equal(ui.formatBytes(1536), "1.5 KiB");
  assert.equal(ui.formatBytes(1073741824), "1 GiB");
  assert.equal(ui.formatBytes(NaN), "—");
  assert.equal(ui.formatDuration(null), "—");
  assert.equal(ui.formatDuration(61), "1m 1s");
  assert.equal(ui.formatDuration(0), "0s");
});

test("directory navigation preserves literal names and resolves parent folders", () => {
  assert.equal(
    typeof ui.joinPath,
    "function",
    "remote navigation is not implemented",
  );
  assert.equal(ui.joinPath("/", "song #1.mp3"), "/song #1.mp3");
  assert.equal(ui.joinPath("/music/", "live"), "/music/live");
  assert.equal(ui.parentPath("/music/live"), "/music");
  assert.equal(ui.parentPath("/music"), "/");
  assert.equal(ui.parentPath("/"), "/");
});

test("throughput graphs separate plain and gzip samples and exclude invalid numbers", () => {
  assert.equal(
    typeof ui.throughputSeries,
    "function",
    "throughput graph preparation is not implemented",
  );
  assert.deepEqual(
    ui.throughputSeries([
      { elapsed: 0, speed_bps: 0, mode: "plain" },
      { elapsed: 1, speed_bps: 2097152, mode: "plain" },
      { elapsed: 0.5, speed_bps: 1048576, mode: "gzip" },
      { elapsed: -1, speed_bps: 1048576, mode: "gzip" },
      { elapsed: 1, speed_bps: NaN, mode: "gzip" },
    ]),
    {
      plain: [
        { x: 0, y: 0 },
        { x: 1, y: 2 },
      ],
      gzip: [{ x: 0.5, y: 1 }],
    },
  );
});

test("comparison calculates compression reduction and preserves slower end-to-end results", () => {
  assert.equal(
    typeof ui.comparisonMetrics,
    "function",
    "benchmark comparison is not implemented",
  );
  assert.deepEqual(
    ui.comparisonMetrics({
      runs: [
        {
          mode: "plain",
          source_bytes: 1000,
          payload_bytes: 1000,
          total_seconds: 2,
          throughput_mib_s: 4,
          verified: true,
        },
        {
          mode: "gzip",
          source_bytes: 1000,
          payload_bytes: 800,
          total_seconds: 3,
          throughput_mib_s: 5,
          verified: true,
        },
      ],
    }),
    { reduction: 20, timeSaved: -1, verified: true },
  );
  assert.deepEqual(ui.comparisonMetrics({ runs: [] }), {
    reduction: null,
    timeSaved: null,
    verified: false,
  });
});

test('benchmark table shows complete preparation, including setup, separately from FTP time', () => {
  assert.equal(typeof ui.benchmarkRowValues, 'function', 'benchmark table formatting is not implemented');
  const values = ui.benchmarkRowValues({
    mode: 'gzip', payload_bytes: 512, compression_seconds: 0.125,
    preparation_seconds: 2.3, transfer_seconds: 1.2, total_seconds: 3.5,
    throughput_mib_s: 4, effective_mib_s: 2, verified: true,
  });
  assert.deepEqual(values, ['Gzip', '512 B', '2.300s', '1.200s', '3.500s', '4.00 MiB/s', '2.00 MiB/s', 'Yes']);
});

test('a response from the previous session cannot restore files after logout', async () => {
  assert.equal(typeof ui.createSessionGate, 'function', 'session response protection is not implemented');
  const gate = ui.createSessionGate();
  let resolve;
  const pending = gate.protect(new Promise(done => { resolve = done; }));
  gate.invalidate();
  const rejection = assert.rejects(pending, error => error.stale === true);
  resolve({job: {id: 'old-job'}});
  await rejection;
  assert.deepEqual(await gate.protect(Promise.resolve({job: {id: 'new-job'}})), {job: {id: 'new-job'}});
});
