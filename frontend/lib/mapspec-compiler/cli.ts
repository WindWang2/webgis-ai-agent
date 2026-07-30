#!/usr/bin/env tsx
import * as fs from "fs";
import * as path from "path";
import { compileMapSpec } from "./compiler";
import { MapSpec, SpatialMetaProfile } from "./types";

function parseArgs(): {
  inputPath?: string;
  profilePath?: string;
  outDir?: string;
} {
  const args = process.argv.slice(2);
  let inputPath: string | undefined;
  let profilePath: string | undefined;
  let outDir: string | undefined;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--input" || args[i] === "-i") {
      inputPath = args[++i];
    } else if (args[i] === "--profile" || args[i] === "-p") {
      profilePath = args[++i];
    } else if (args[i] === "--out-dir" || args[i] === "-o") {
      outDir = args[++i];
    } else if (!inputPath && !args[i].startsWith("-")) {
      inputPath = args[i];
    }
  }

  return { inputPath, profilePath, outDir };
}

function main() {
  const { inputPath, profilePath, outDir } = parseArgs();

  if (!inputPath) {
    console.error("Usage: npx tsx cli.ts <mapspec.json> [--profile profile.json] [--out-dir <output_dir>]");
    process.exit(1);
  }

  try {
    const rawSpec = fs.readFileSync(inputPath, "utf-8");
    const spec: MapSpec = JSON.parse(rawSpec);

    let profile: SpatialMetaProfile | undefined;
    if (profilePath && fs.existsSync(profilePath)) {
      const rawProfile = fs.readFileSync(profilePath, "utf-8");
      profile = JSON.parse(rawProfile);
    }

    const result = compileMapSpec(spec, profile);

    if (outDir) {
      if (!fs.existsSync(outDir)) {
        fs.mkdirSync(outDir, { recursive: true });
      }

      fs.writeFileSync(
        path.join(outDir, "style.json"),
        JSON.stringify(result.style, null, 2),
        "utf-8"
      );
      fs.writeFileSync(
        path.join(outDir, "index.html"),
        result.html,
        "utf-8"
      );
      fs.writeFileSync(
        path.join(outDir, "legend.json"),
        JSON.stringify(result.legend, null, 2),
        "utf-8"
      );
      fs.writeFileSync(
        path.join(outDir, "compile-report.json"),
        JSON.stringify(result.report, null, 2),
        "utf-8"
      );

      console.log(`Compilation complete. Output written to ${outDir}`);
    } else {
      console.log(JSON.stringify(result.report, null, 2));
    }

    if (!result.report.success) {
      process.exit(1);
    }
  } catch (err: any) {
    console.error(`Compilation error: ${err.message}`);
    process.exit(1);
  }
}

if (require.main === module) {
  main();
}
