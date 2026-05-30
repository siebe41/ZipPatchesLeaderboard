# deploy.ps1 — Zip Patchlings deploy script
# Run from your project folder in VS Code terminal

$rg = "VisualStudioOnline-6E968DBE716744219BC76520BB9B67F1"
$app = "zip-patchlings-api"
$url = "https://zip-patchlings-api-bddrekdscygta7ar.centralus-01.azurewebsites.net/"
$zip = "app.zip"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Zip Patchlings — Deploy Script" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1 — Clean old zip
if (Test-Path $zip) {
    Remove-Item $zip -Force
    Write-Host "[1/5] Removed old $zip" -ForegroundColor Yellow
} else {
    Write-Host "[1/5] No old zip to clean" -ForegroundColor Gray
}

# Step 2 — Zip project files (exclude junk)
Write-Host "[2/5] Zipping project files..." -ForegroundColor Yellow
$filesToZip = Get-ChildItem -Path . -Exclude "*.zip", ".git", "__pycache__", ".vscode", "*.ps1", "node_modules" | Where-Object { $_.Name -ne ".git" }
Compress-Archive -Path $filesToZip.FullName -DestinationPath $zip -Force
Write-Host "       Created $zip" -ForegroundColor Green

# Step 3 — Deploy
Write-Host "[3/5] Deploying to Azure..." -ForegroundColor Yellow
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
az webapp deploy --resource-group $rg --name $app --src-path $zip --type zip
$stopwatch.Stop()
Write-Host "       Deploy completed in $([math]::Round($stopwatch.Elapsed.TotalSeconds, 1))s" -ForegroundColor Green

# Step 4 — Restart
Write-Host "[4/5] Restarting app..." -ForegroundColor Yellow
az webapp restart --resource-group $rg --name $app
Write-Host "       App restarted" -ForegroundColor Green

# Step 5 — Open dashboard
Write-Host "[5/5] Opening dashboard in 10 seconds..." -ForegroundColor Yellow
Start-Sleep -Seconds 10
Start-Process $url
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deploy complete!" -ForegroundColor Green
Write-Host "  $url" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
