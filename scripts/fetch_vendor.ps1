<#
.SYNOPSIS
    Fetches ffmpeg.exe, ffprobe.exe, and libmpv-2.dll into vendor/.

.DESCRIPTION
    Run from anywhere; resolves paths relative to this script's own location,
    so `vendor/` always ends up at the repo root regardless of your current
    directory:

        powershell -ExecutionPolicy Bypass -File scripts/fetch_vendor.ps1

    Needs 7-Zip (7z.exe) on PATH or at its standard install location --
    only used to unpack the mpv build below, which ships as .7z (get it
    from https://7-zip.org or `winget install 7zip.7zip` if missing).

    Sources, and why: ffmpeg/ffprobe come from BtbN's GPL Windows builds
    (CLAUDE.md calls for a GPL ffmpeg with libx264 bundled in); libmpv comes
    from shinchiro's mpv-winbuild-cmake "dev" build, the commonly-used
    source for python-mpv on Windows. Both repos' "latest release" rotates
    its assets over time rather than keeping old ones at a fixed URL
    forever, so this resolves the current release's asset list at fetch
    time instead of hardcoding a specific filename that will eventually
    404.
#>

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$vendorDir = Join-Path $repoRoot "vendor"
$tempDir = Join-Path $vendorDir "_fetch_tmp"
New-Item -ItemType Directory -Force -Path $vendorDir | Out-Null
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

function Find-SevenZip {
    $onPath = Get-Command 7z.exe -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    $standard = "$env:ProgramFiles\7-Zip\7z.exe"
    if (Test-Path $standard) { return $standard }
    throw "7-Zip (7z.exe) not found. Install it from https://7-zip.org or 'winget install 7zip.7zip', then re-run this script."
}

try {
    Write-Host "Fetching ffmpeg/ffprobe (BtbN GPL build)..."
    $ffmpegZip = Join-Path $tempDir "ffmpeg.zip"
    Invoke-WebRequest -Uri "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" -OutFile $ffmpegZip
    $ffmpegExtract = Join-Path $tempDir "ffmpeg_extracted"
    Expand-Archive -Path $ffmpegZip -DestinationPath $ffmpegExtract -Force
    $ffmpegBin = Get-ChildItem -Path $ffmpegExtract -Recurse -Directory -Filter "bin" | Select-Object -First 1
    if (-not $ffmpegBin) {
        throw "Couldn't find a bin/ folder inside the downloaded ffmpeg build -- its layout may have changed."
    }
    Copy-Item (Join-Path $ffmpegBin.FullName "ffmpeg.exe") $vendorDir -Force
    Copy-Item (Join-Path $ffmpegBin.FullName "ffprobe.exe") $vendorDir -Force
    Write-Host "  -> vendor/ffmpeg.exe, vendor/ffprobe.exe"

    Write-Host "Fetching libmpv (shinchiro mpv-winbuild-cmake dev build)..."
    $sevenZip = Find-SevenZip
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"
    $mpvAsset = $release.assets | Where-Object { $_.name -match '^mpv-dev-x86_64-\d+-git-[0-9a-f]+\.7z$' } | Select-Object -First 1
    if (-not $mpvAsset) {
        throw "Couldn't find a matching mpv-dev-x86_64-*.7z asset in the latest shinchiro/mpv-winbuild-cmake release."
    }
    $mpvArchive = Join-Path $tempDir "mpv.7z"
    Invoke-WebRequest -Uri $mpvAsset.browser_download_url -OutFile $mpvArchive
    $mpvExtract = Join-Path $tempDir "mpv_extracted"
    & $sevenZip x $mpvArchive "-o$mpvExtract" -y | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "7z.exe failed to extract $mpvArchive (exit code $LASTEXITCODE)."
    }
    Copy-Item (Join-Path $mpvExtract "libmpv-2.dll") $vendorDir -Force
    Write-Host "  -> vendor/libmpv-2.dll (from $($mpvAsset.name))"
}
finally {
    Remove-Item -Recurse -Force $tempDir -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Done. vendor/ now has:"
Get-ChildItem $vendorDir | Format-Table Name, Length
