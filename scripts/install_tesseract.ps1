#requires -RunAsAdministrator

<#
.SYNOPSIS
    Tesseract OCR Kurulum Betiği
.DESCRIPTION
    Tesseract-OCR'ını indirir, kurar ve PATH'e ekler.
.PARAMETER Version
    Tesseract sürümü (örnek: 5.4.0)
.PARAMETER Edition
    Tesseract sürümü (örnek: 64, x86)
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_tesseract.ps1
#>

[CmdletBinding()]
param(
    [string]$Version = "5.4.0",
    [string]$Edition = "64",
    [string]$BaseUrl = "https://github.com/UB-Mannheim/tesseract/releases/download",
    [string]$InstallDir = "C:\Program Files\Tesseract-OCR"
)

# Check if running as administrator
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $isAdmin) {
    Write-Error "Bu betik Yönetici yetkileri ile çalıştırılmalıdır!"
    exit 1
}

# Determine file name based on version and edition
$versionParts = $Version -split "\."
$major = $versionParts[0]
$minor = $versionParts[1]
$patch = $versionParts[2]

# Build the download URL
$fileName = "tesseract-ocr-w${Edition}-setup-${Version}.exe"
$downloadUrl = "${BaseUrl}/v${Version}/${fileName}"
$installerPath = "$env:TEMP\tesseract-setup.exe"

Write-Host "1. Tesseract OCR kurulum dosyası indiriliyor..." -ForegroundColor Cyan
try {
    Invoke-WebRequest -Uri $downloadUrl -OutFile $installerPath
    Write-Host "   İndirme tamamlandı: $installerPath" -ForegroundColor Green
}
catch {
    Write-Error "İndirme başarısız: $($_.Exception.Message)"
    exit 1
}

# Install Tesseract silently
Write-Host "2. Tesseract OCR sessiz modda kuruluyor..." -ForegroundColor Cyan
try {
    $installProcess = Start-Process -FilePath $installerPath -ArgumentList "/S", "/D=$installDir" -Wait -PassThru
    $installerProcess = Get-Process -Name "tesseract*" -ErrorAction SilentlyContinue
    while ($installerProcess) {
        Start-Sleep -Seconds 5
        $installerProcess = Get-Process -Name "tesseract*" -ErrorAction SilentlyContinue
    }
    Write-Host "   Kurulum tamamlandı." -ForegroundColor Green
}
catch {
    Write-Error "Kurulum başarısız: $($_.Exception.Message)"
    exit 1
}

# Add Tesseract to PATH
Write-Host "3. Sistem PATH ortam değişkeni güncelleniyor..." -ForegroundColor Cyan
$currentPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::Machine)
if ($currentPath -notlike "*$installDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$installDir", [EnvironmentVariableTarget]::Machine)
    Write-Host "   Tesseract PATH'e eklendi." -ForegroundColor Green
}
else {
    Write-Host "   Tesseract zaten PATH'te mevcut." -ForegroundColor Yellow
}

# Clean up
Remove-Item $installerPath -Force

Write-Host "Islem tamamlandi! Degisikliklerin aktif olmasi icin terminali yeniden baslatip 'tesseract --version' yazarak test edebilirsiniz." -ForegroundColor Green
