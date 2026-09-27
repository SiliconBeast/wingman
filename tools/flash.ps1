# Flash a Zephyr image to the phyBOARD-RT1170 QSPI NOR over USB-OTG with blhost (Windows).
# Same steps as tools/flash.sh. blhost talks USB HID, which Windows supports without drivers.
#
#   powershell -ExecutionPolicy Bypass -File tools\flash.ps1 build\zephyr\zephyr.bin [ivt_flashloader.bin]
#
# Before running (you do these):
#   1. Power off. S5 -> USB-OTG Serial Downloader. S7 OFF.
#   2. Micro-USB from the board's USB-OTG port to this PC.
#   3. Power on (X9, supplied 5 V PSU).
# blhost:  py -m venv $HOME\spsdk-venv; & $HOME\spsdk-venv\Scripts\Activate.ps1; pip install spsdk
param(
    [Parameter(Mandatory = $true)][string]$Image,
    [string]$Loader = $(if ($env:IVT_FLASHLOADER) { $env:IVT_FLASHLOADER } else { Join-Path $PSScriptRoot "ivt_flashloader.bin" })
)
$ErrorActionPreference = "Stop"

$ROM = "0x1fc9:0x013d"
$LDR = "0x15a2:0x0073"
$FlashBase = "0x30000000"
$MinErase = 0x80000

function Fail($msg) {
    Write-Host ""
    Write-Host "FLASH FAILED: $msg" -ForegroundColor Red
    Write-Host @"

Checks, in order:
  - S5 really in USB-OTG Serial Downloader position, S7 OFF, board power-cycled after moving S5
  - cable in the USB-OTG micro-USB port (not X15 console); try another cable / USB port
  - Device Manager -> Human Interface Devices should show a new HID device when the board powers on
"@
    exit 1
}

function Run-Blhost {
    Write-Host "+ blhost $($args -join ' ')"
    $out = & blhost @args 2>&1
    $code = $LASTEXITCODE
    $out | ForEach-Object { Write-Host "    $_" }
    if ($code -ne 0) { Fail "blhost $($args -join ' ')" }
}

if (-not (Test-Path $Image)) { Fail "image not found: $Image" }
if (-not (Test-Path $Loader)) { Fail "flashloader not found: $Loader (download ivt_flashloader.bin from PHYTEC's Pre-Built Binaries page, or pass -Loader)" }
if (-not (Get-Command blhost -ErrorAction SilentlyContinue)) { Fail "blhost not in PATH (activate the spsdk venv)" }

$size = (Get-Item $Image).Length
$erase = $MinErase
if ($size -gt $erase) {
    $erase = [math]::Ceiling($size / 0x10000) * 0x10000
    Write-Host ("image is {0} bytes: erase region bumped to 0x{1:x}" -f $size, $erase)
}
$eraseHex = "0x{0:x}" -f [int]$erase
Write-Host "image $Image : $size bytes, erasing $eraseHex bytes at $FlashBase"

Write-Host "==> 1/4 talk to the boot ROM"
Run-Blhost -u $ROM get-property 1

Write-Host "==> 2/4 load the flashloader into RAM"
Run-Blhost -u $ROM load-image $Loader

Write-Host "    waiting for the flashloader to enumerate..."
$ok = $false
for ($i = 0; $i -lt 10; $i++) {
    Start-Sleep -Milliseconds 500
    & blhost -u $LDR get-property 1 *> $null
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
}
if (-not $ok) { Fail "flashloader ($LDR) did not appear after load-image" }
Run-Blhost -u $LDR get-property 1

Write-Host "==> 3/4 configure QSPI NOR (FlexSPI)"
Run-Blhost -u $LDR fill-memory 0x00002000 4 0xCF900001
Run-Blhost -u $LDR configure-memory 9 0x00002000
Run-Blhost -u $LDR fill-memory 0x00002000 4 0xC0000007
Run-Blhost -u $LDR configure-memory 9 0x00002000

Write-Host "==> 4/4 erase + write"
Run-Blhost -u $LDR flash-erase-region $FlashBase $eraseHex
Run-Blhost -u $LDR write-memory $FlashBase $Image

Write-Host @"

FLASHED OK. Now (you):
  1. Power off.
  2. S5 -> QSPI Flash (normal boot).
  3. Power on and watch the console (X15, lower COM port, 115200 8N1).
"@ -ForegroundColor Green
