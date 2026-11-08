"""Record machine and tool versions to out/machine.json."""
import json
import subprocess as sp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, TOOLS, VIBEPILER, VIBEPILER_REPO  # noqa: E402


def r(*c):
    return sp.run(c, capture_output=True, text=True).stdout.strip()


m = dict(cpu=r("sysctl", "-n", "machdep.cpu.brand_string"), cores=int(r("sysctl", "-n", "hw.ncpu")),
         mem_gb=int(r("sysctl", "-n", "hw.memsize")) // 2**30, os="macOS " + r("sw_vers", "-productVersion"),
         node=r("node", "--version"), python=r("python3", "--version"), vibepiler=r(str(VIBEPILER), "--version"),
         vibepiler_commit=r("git", "-C", str(VIBEPILER_REPO), "rev-parse", "HEAD"),
         tools={k: json.loads((TOOLS / "node_modules" / k / "package.json").read_text())["version"]
                for k in ["knip", "ts-prune", "ts-unused-exports", "depcheck", "jscpd", "madge", "eslint",
                          "eslint-plugin-sonarjs", "@typescript-eslint/parser", "typescript", "pnpm"]},
         notes="machine shared with two other evaluation agents running concurrently; timings are noisy; "
               "OS file cache not flushed between runs")
OUT.mkdir(exist_ok=True)
(OUT / "machine.json").write_text(json.dumps(m, indent=1))
