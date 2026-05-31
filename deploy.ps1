# deploy.ps1 — Tar/SCP Synology deploy script
# Run from your project folder in VS Code terminal

$nasUser   = "siebe41"
$nasIp     = "192.168.1.108"
$sftpPath  = "/docker/leaderboard-api"          # What SCP sees (Keep this as is)
$sshPath   = "/volume1/docker/leaderboard-api"  # What the SSH terminal sees (Add /volume1 here)
$publicUrl = "https://siebe41.synology.me"
$archive   = "app.tar"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   API — Synology Deploy Script" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1 — Clean old local archive
if (Test-Path $archive) {
    Remove-Item $archive -Force
    Write-Host "[1/5] Removed old local $archive" -ForegroundColor Yellow
} else {
    Write-Host "[1/5] No old local archive to clean" -ForegroundColor Gray
}

# Step 2 — Tar project files
Write-Host "[2/5] Bundling project files..." -ForegroundColor Yellow
tar.exe -cf $archive --exclude=".git" --exclude="data" --exclude="__pycache__" --exclude=".vscode" --exclude="node_modules" .
if ($LASTEXITCODE -ne 0) { throw "Failed to create archive." }
Write-Host "       Created $archive" -ForegroundColor Green

# Step 3 — Transfer to Synology
Write-Host "[3/5] Transferring archive to Synology via SCP..." -ForegroundColor Yellow
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
# Uses the SFTP virtual path
scp $archive "${nasUser}@${nasIp}:${sftpPath}/$archive"
if ($LASTEXITCODE -ne 0) { throw "SCP Transfer Failed. Check Synology SFTP settings and folder permissions." }
$stopwatch.Stop()
Write-Host "       Transfer completed in $([math]::Round($stopwatch.Elapsed.TotalSeconds, 1))s" -ForegroundColor Green

# Step 4 — Extract and Restart Container
Write-Host "[4/5] Extracting files and restarting application..." -ForegroundColor Yellow
# Uses the absolute Linux physical path
ssh -t "${nasUser}@${nasIp}" "cd ${sshPath} && tar -xf $archive && rm $archive && sudo env PATH=/usr/local/bin:/usr/bin:/bin docker-compose up -d --build"
if ($LASTEXITCODE -ne 0) { throw "SSH Command Failed. Container may not have restarted." }
Write-Host "       Container stack successfully restarted" -ForegroundColor Green

# Step 5 — Open dashboard
Write-Host "[5/5] Opening external dashboard in 5 seconds..." -ForegroundColor Yellow
Start-Sleep -Seconds 5
Start-Process $publicUrl
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "   Deploy complete!" -ForegroundColor Green
Write-Host "   $publicUrl" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Green
Write-Host ""