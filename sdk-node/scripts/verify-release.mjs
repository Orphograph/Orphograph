#!/usr/bin/env node
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const root = new URL("..", import.meta.url).pathname;
const scratch = await mkdtemp(join(tmpdir(), "orphograph-npm-release-"));

function run(command, args, cwd = root) {
  const result = spawnSync(command, args, { cwd, encoding: "utf8" });
  if (result.status !== 0) {
    process.stderr.write(result.stdout || "");
    process.stderr.write(result.stderr || "");
    throw new Error(`${command} ${args.join(" ")} failed with ${result.status}`);
  }
  return result;
}

try {
  const packResult = run("npm", ["pack", "--json", "--pack-destination", scratch]);
  const packed = JSON.parse(packResult.stdout);
  if (!Array.isArray(packed) || packed.length !== 1) throw new Error("npm pack returned no package");
  const pkg = packed[0];
  const names = new Set((pkg.files || []).map((entry) => entry.path));
  for (const required of ["package.json", "README.md", "LICENSE", "dist/index.js", "dist/cli.js", "dist/index.d.ts"]) {
    if (!names.has(required)) throw new Error(`package is missing ${required}`);
  }
  for (const forbidden of [".env", ".npmrc", "test/", "src/"]) {
    if ([...names].some((name) => name === forbidden || name.startsWith(forbidden))) {
      throw new Error(`package unexpectedly contains ${forbidden}`);
    }
  }

  const tarball = join(scratch, pkg.filename);
  const installRoot = join(scratch, "install");
  run("npm", ["install", "--prefix", installRoot, tarball]);
  const cli = join(installRoot, "node_modules", ".bin", "orphograph");
  const helpResult = run(cli, ["--help"]);
  // The CLI intentionally writes usage to stderr. Successful output is the
  // complete stream contract, not stdout alone.
  const help = (helpResult.stdout || "") + (helpResult.stderr || "");
  if (!help.includes("Usage:") || !help.includes("Privacy:")) {
    throw new Error("installed CLI help failed its contract check");
  }
  const installed = JSON.parse(await readFile(join(installRoot, "node_modules", "orphograph", "package.json"), "utf8"));
  if (installed.version !== pkg.version) throw new Error("installed version differs from tarball version");
  console.log(`release check passed: ${pkg.filename}, ${pkg.size} bytes, ${pkg.files.length} files`);
} finally {
  await rm(scratch, { recursive: true, force: true });
}
