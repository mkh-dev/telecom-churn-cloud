# Membre 4 — Captures du tableau de bord avec Microsoft Edge sans interface (Windows, rien à installer).
#
#   .\docs\captures\capture-dashboard.ps1 -Url https://stchurnpipe.z28.web.core.windows.net/
#   .\docs\captures\capture-dashboard.ps1 -Url http://127.0.0.1:8080/?demo=1 -Prefix 01-dashboard-demo
#
# Produit <Prefix>.png (bureau, 1440 px), <Prefix>-sombre.png (thème sombre) et 03-dashboard-mobile.png (390 px).
# L'URL publique du site est affichée par infra/preuves.sh (docs/captures/preuves/dashboard-url.txt).
param(
  [Parameter(Mandatory = $true)][string]$Url,
  [string]$Prefix = "02-dashboard-live"
)
$ErrorActionPreference = "Stop"
$edge = @(
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { throw "Microsoft Edge introuvable" }

$dir = $PSScriptRoot
$profileDir = Join-Path $env:TEMP "edge-capture-churn"

function Save-Shot([string]$target, [string]$file, [string]$size, [string[]]$extra = @()) {
  $out = Join-Path $dir $file
  # virtual-time-budget : laisse le temps à /api/recent et à Chart.js (animations comprises)
  $edgeArgs = @("--headless=new", "--disable-gpu", "--hide-scrollbars", "--user-data-dir=$profileDir",
            "--virtual-time-budget=12000", "--window-size=$size", "--screenshot=$out") + $extra + @($target)
  # Edge écrit des messages de diagnostic sur stderr : ne pas les traiter comme des erreurs
  $ErrorActionPreference = "Continue"
  & $edge $edgeArgs 2>&1 | Out-Null
  if (Test-Path $out) { Write-Host "OK  $file" } else { Write-Warning "échec : $file" }
}

Save-Shot $Url "$Prefix.png" "1440,2300"
Save-Shot $Url "$Prefix-sombre.png" "1440,2300" @("--force-dark-mode", "--blink-settings=preferredColorScheme=0")
# Mobile : Edge sans interface ne descend pas sous ~500 px de large. On affiche donc le site dans un
# cadre de 390 px (largeur d'un téléphone : les règles CSS mobiles s'appliquent), puis on recadre l'image.
# La page du cadre est servie en http://localhost par un mini-serveur temporaire : depuis un fichier
# local (file://), le navigateur bloque les requêtes de la page vers le réseau et les données ne chargent pas.
$safeUrl = [System.Security.SecurityElement]::Escape($Url)
$html = "<!DOCTYPE html><html><body style=""margin:0;background:#fff""><iframe src=""$safeUrl"" " +
        "style=""width:390px;height:2600px;border:0;display:block""></iframe></body></html>"
$port = 18765
$server = Start-Job -ArgumentList $html, $port -ScriptBlock {
  param($html, $port)
  $l = New-Object System.Net.HttpListener
  $l.Prefixes.Add("http://127.0.0.1:$port/")
  $l.Start()
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($html)
  $wait = 30000                       # s'arrête seul après 30 s sans requête, puis 5 s après la dernière
  while ($true) {
    $task = $l.GetContextAsync()
    if (-not $task.Wait($wait)) { break }
    $ctx = $task.Result
    $ctx.Response.ContentType = "text/html; charset=utf-8"
    $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    $ctx.Response.Close()
    $wait = 5000
  }
  $l.Stop()
}
Start-Sleep -Seconds 2
Save-Shot "http://127.0.0.1:$port/" "03-dashboard-mobile.png" "600,2600"
Wait-Job $server -Timeout 40 | Out-Null; Remove-Job $server -Force
$mobile = Join-Path $dir "03-dashboard-mobile.png"
if (Test-Path $mobile) {
  Add-Type -AssemblyName System.Drawing
  $img = [System.Drawing.Image]::FromFile($mobile)
  $crop = New-Object System.Drawing.Bitmap 390, $img.Height
  $g = [System.Drawing.Graphics]::FromImage($crop)
  $g.DrawImage($img, (New-Object System.Drawing.Rectangle 0, 0, 390, $img.Height),
               (New-Object System.Drawing.Rectangle 0, 0, 390, $img.Height), [System.Drawing.GraphicsUnit]::Pixel)
  $g.Dispose(); $img.Dispose()
  $crop.Save($mobile, [System.Drawing.Imaging.ImageFormat]::Png); $crop.Dispose()
}

Write-Host ""
Write-Host "Vérifier sur les images que la pastille en haut à droite indique « En direct » (et pas « Mode démo »)."
