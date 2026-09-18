# Windows: git checkouts of .claude/skills/* are often text files, not symlinks.
# Copy .agents/skills/<name>/ into .claude/skills/<name>/ so Claude Code can load them.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$srcRoot = Join-Path $root ".agents\skills"
$dstRoot = Join-Path $root ".claude\skills"
if (-not (Test-Path $srcRoot)) { throw ".agents/skills not found: $srcRoot" }
New-Item -ItemType Directory -Force -Path $dstRoot | Out-Null
Get-ChildItem $srcRoot -Directory | ForEach-Object {
    $dst = Join-Path $dstRoot $_.Name
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    Copy-Item -Recurse $_.FullName $dst
    Write-Host "copied $($_.Name)"
}
