#!/usr/bin/env node
// Match the skills CLI's folder hash: locale-sorted relative paths plus bytes.
import { createHash } from "node:crypto";
import { readFile, readdir, writeFile } from "node:fs/promises";
import { resolve, join, relative, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const update = process.argv.slice(2);
if (update.length > 1 || (update.length === 1 && update[0] !== "--update-lock")) {
  console.error("Usage: node scripts/check_repo_skills.mjs [--update-lock]");
  process.exit(1);
}

async function folderHash(directory) {
  const files = [];
  async function collect(current) {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      if (entry.name === ".git" || entry.name === "node_modules") continue;
      const path = join(current, entry.name);
      if (entry.isSymbolicLink()) throw new Error(`Skill files must be versioned, not symlinked: ${path}`);
      if (entry.isDirectory()) await collect(path);
      else if (entry.isFile()) files.push({ name: relative(directory, path).split("\\").join("/"), path });
    }
  }
  await collect(directory);
  files.sort((a, b) => a.name.localeCompare(b.name));
  const hash = createHash("sha256");
  for (const file of files) {
    hash.update(file.name);
    hash.update(await readFile(file.path));
  }
  return hash.digest("hex");
}

try {
  const lockPath = join(root, "skills-lock.json");
  const lock = JSON.parse(await readFile(lockPath, "utf8"));
  if (lock.version !== 1 || !lock.skills || typeof lock.skills !== "object" || Array.isArray(lock.skills)) {
    throw new Error("Expected a version 1 skills lock with a skills map.");
  }
  const skillsRoot = join(root, ".agents", "skills");
  const folders = (await readdir(skillsRoot, { withFileTypes: true }))
    .filter((entry) => entry.isDirectory() || entry.isSymbolicLink());
  const installed = folders.map((entry) => entry.name).sort();
  const names = Object.keys(lock.skills).sort();
  if (JSON.stringify(installed) !== JSON.stringify(names)) {
    throw new Error("Skill folders and lock entries differ. Review missing/extra folders (including ignored legacy skills); see docs/developer-skills.md.");
  }
  const stale = [];
  const refreshed = {};
  for (const name of names) {
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name) || name.length > 64) throw new Error(`Invalid skill name: ${name}`);
    const entry = lock.skills[name];
    if (entry.sourceType !== "local" || entry.source !== `./.agents/skills/${name}`) {
      throw new Error(`Expected repository-local source for ${name}`);
    }
    const directory = join(skillsRoot, name);
    const info = folders.find((item) => item.name === name);
    if (info.isSymbolicLink()) throw new Error(`Skill folder must not be symlinked: ${name}`);
    await readFile(join(directory, "SKILL.md"));
    const computedHash = await folderHash(directory);
    if (entry.computedHash !== computedHash) stale.push(name);
    refreshed[name] = { ...entry, computedHash };
  }
  if (update.length) {
    await writeFile(lockPath, JSON.stringify({ ...lock, skills: refreshed }, null, 2) + "\n");
    console.log(`Updated ${stale.length} hashes; ${names.length} repository skills indexed.`);
  } else if (stale.length) {
    throw new Error(`Stale skill hashes: ${stale.join(", ")}. After reviewing edits, run with --update-lock.`);
  } else {
    console.log(`PASS: ${names.length} repository skill folders, sources, and content hashes match.`);
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
