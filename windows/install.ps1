<#
.SYNOPSIS
  ExtScope installer for Windows (user or system scope).
.PARAMETER Mode     user (default; %LOCALAPPDATA%\Programs\extscope) or system (%ProgramFiles%\extscope, needs admin).
.PARAMETER Prefix   Explicit install prefix.
.PARAMETER Symlink  Link src to this repo instead of copying (needs Developer Mode or admin).
.PARAMETER Uninstall Remove binary and library; config/data are preserved.
.PARAMETER DryRun   Print actions only.
#>
[CmdletBinding()]
param(
    [ValidateSet('user', 'system')][string]$Mode = 'user',
    [string]$Prefix,
    [switch]$Symlink,
    [switch]$Uninstall,
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Version = '0.1.0a6'

if (-not $Prefix) {
    $Prefix = if ($Mode -eq 'system') { Join-Path $env:ProgramFiles 'extscope' }
              else { Join-Path $env:LOCALAPPDATA 'Programs\extscope' }
}
$BinDir = Join-Path $Prefix 'bin'
$LibDir = Join-Path $Prefix 'lib\extscope'

function Invoke-Step([string]$Text, [scriptblock]$Action) {
    if ($DryRun) { Write-Host "[DRY-RUN] $Text" } else { & $Action }
}

if ($Uninstall) {
    Invoke-Step "remove $BinDir\extscope.cmd and $LibDir" {
        Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $BinDir 'extscope.cmd'), (Join-Path $BinDir 'extscope-dist.cmd'), (Join-Path $BinDir 'extscope-container.cmd'), (Join-Path $BinDir 'extscope-tools.cmd')
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Prefix 'lib\extscope-dist'), (Join-Path $Prefix 'lib\extscope-modules'), (Join-Path $Prefix 'lib\extscope-container'), (Join-Path $Prefix 'lib\extscope-tools')
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $LibDir
    }
    Write-Host "Removed. Config/data under %ProgramData% / %APPDATA% / %LOCALAPPDATA% were preserved."
    return
}

# Python >= 3.11 check
$py = $null
foreach ($c in @(@('py', '-3'), @('python'))) {
    if (Get-Command $c[0] -ErrorAction SilentlyContinue) {
        $ok = & $c[0] @($c[1..($c.Count - 1)]) -c "import sys; print(int(sys.version_info >= (3, 11)))" 2>$null
        if ($ok -eq '1') { $py = $c; break }
    }
}
if (-not $py) { throw 'Python >= 3.11 is required but was not found in PATH.' }

if ($Mode -eq 'system') {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
             ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { throw 'Mode system requires an elevated (Administrator) PowerShell.' }
}

Write-Host "ExtScope $Version -> $Prefix ($Mode)"
Invoke-Step "mkdir $BinDir, $LibDir" { New-Item -ItemType Directory -Force $BinDir, $LibDir | Out-Null }
Invoke-Step "install src to $LibDir\src" {
    $dst = Join-Path $LibDir 'src'
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $dst
    if ($Symlink) { New-Item -ItemType SymbolicLink -Path $dst -Target (Join-Path $Repo 'src') | Out-Null }
    else { Copy-Item -Recurse (Join-Path $Repo 'src') $dst }
}
Invoke-Step "copy launcher to $BinDir\extscope.cmd" {
    Copy-Item -Force (Join-Path $PSScriptRoot 'extscope.cmd') (Join-Path $BinDir 'extscope.cmd')
}

Invoke-Step "install extscope-dist to $Prefix\lib\extscope-dist" {
    $dl = Join-Path $Prefix 'lib\extscope-dist'
    New-Item -ItemType Directory -Force $dl | Out-Null
    $dsrc = Join-Path $dl 'src'
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $dsrc
    if ($Symlink) { New-Item -ItemType SymbolicLink -Path $dsrc -Target (Join-Path $Repo 'extscope-dist\src') | Out-Null }
    else { Copy-Item -Recurse (Join-Path $Repo 'extscope-dist\src') $dsrc }
    $ml = Join-Path $Prefix 'lib\extscope-modules'
    New-Item -ItemType Directory -Force $ml | Out-Null
    $msrc = Join-Path $ml 'src'
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $msrc
    if ($Symlink) { New-Item -ItemType SymbolicLink -Path $msrc -Target (Join-Path $Repo 'extscope-modules\src') | Out-Null }
    else { Copy-Item -Recurse (Join-Path $Repo 'extscope-modules\src') $msrc }
    Copy-Item -Force (Join-Path $PSScriptRoot 'extscope-dist.cmd') (Join-Path $BinDir 'extscope-dist.cmd')
}

foreach ($pkg in 'extscope-container', 'extscope-tools') {
    Invoke-Step "install $pkg" {
        $pl = Join-Path $Prefix "lib\$pkg"
        New-Item -ItemType Directory -Force $pl | Out-Null
        $psrc = Join-Path $pl 'src'
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $psrc
        if ($Symlink) { New-Item -ItemType SymbolicLink -Path $psrc -Target (Join-Path $Repo "$pkg\src") | Out-Null }
        else { Copy-Item -Recurse (Join-Path $Repo "$pkg\src") $psrc }
        Copy-Item -Force (Join-Path $PSScriptRoot "$pkg.cmd") (Join-Path $BinDir "$pkg.cmd")
    }
}

# Add BinDir to PATH of the target scope if missing
$target = if ($Mode -eq 'system') { 'Machine' } else { 'User' }
$cur = [Environment]::GetEnvironmentVariable('Path', $target)
if (($cur -split ';') -notcontains $BinDir) {
    Invoke-Step "add $BinDir to $target PATH" {
        [Environment]::SetEnvironmentVariable('Path', ($cur.TrimEnd(';') + ';' + $BinDir), $target)
    }
    Write-Host 'PATH updated; open a new terminal to use `extscope`.'
}

if (-not $DryRun) { & (Join-Path $BinDir 'extscope.cmd') --help | Select-Object -First 3 }
