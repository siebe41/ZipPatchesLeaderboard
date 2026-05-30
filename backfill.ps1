$base = "https://zip-patchlings-api-bddrekdscygta7ar.centralus-01.azurewebsites.net"

# Reset all data
Write-Host "Resetting..."
Invoke-RestMethod -Uri "$base/reset" -Method POST
Write-Host "Reset complete"

# 2026-04-30 (SEED DAY (register founding members))
Write-Host 'Ingesting 2026-04-30 (SEED DAY (register founding members))...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-04-30", "messages": ["Gavin Speed: 0//0", "Keeran Mistry: 0//0", "Andrew Siebert: 0//0", "Katie Osborn: 0//0", "Brian Hall: 0//0"]}'

# 2026-05-01 (3 scores)
Write-Host 'Ingesting 2026-05-01 (3 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-01", "messages": ["Gavin Speed: 15//8", "Keeran Mistry: 10//13", "Andrew Siebert: 10//55"]}'

# 2026-05-02 (4 scores)
Write-Host 'Ingesting 2026-05-02 (4 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-02", "messages": ["Gavin Speed: 11//172", "Keeran Mistry: 14//30", "Katie Osborn: 22//118", "Andrew Siebert: 22//27"]}'

# 2026-05-03 (3 scores)
Write-Host 'Ingesting 2026-05-03 (3 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-03", "messages": ["Keeran Mistry: 89//29", "Andrew Siebert: 47//45", "Gavin Speed: 48//128"]}'

# 2026-05-04 (5 scores)
Write-Host 'Ingesting 2026-05-04 (5 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-04", "messages": ["Andrew Siebert: 10//15", "Gavin Speed: 35//15", "Keeran Mistry: 14//11", "Katie Osborn: 27//16", "Crystal Schmidt: 58//7"]}'

# 2026-05-05 (5 scores)
Write-Host 'Ingesting 2026-05-05 (5 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-05", "messages": ["Gavin Speed: 11//10", "Keeran Mistry: 15//23", "Crystal Schmidt: 20//30", "Katie Osborn: 10//19", "Andrew Siebert: 11//19"]}'

# 2026-05-06 (5 scores)
Write-Host 'Ingesting 2026-05-06 (5 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-06", "messages": ["Gavin Speed: 11//13", "Katie Osborn: 10//25", "Keeran Mistry: 15//37", "Crystal Schmidt: 16//126", "Andrew Siebert: 6//11"]}'

# 2026-05-07 (5 scores)
Write-Host 'Ingesting 2026-05-07 (5 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-07", "messages": ["Gavin Speed: 11//39", "Andrew Siebert: 10//19", "Katie Osborn: 9//77", "Keeran Mistry: 14//55", "Crystal Schmidt: 18//38"]}'

# 2026-05-08 (5 scores)
Write-Host 'Ingesting 2026-05-08 (5 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-08", "messages": ["Gavin Speed: 18//57", "Andrew Siebert: 9//27", "Brian Hall: 17//39", "Crystal Schmidt: 33//27", "Keeran Mistry: 23//20"]}'

# 2026-05-09 (1 scores)
Write-Host 'Ingesting 2026-05-09 (1 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-09", "messages": ["Gavin Speed: 62//33"]}'

# 2026-05-10 (3 scores)
Write-Host 'Ingesting 2026-05-10 (3 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-10", "messages": ["Gavin Speed: 64//129", "Andrew Siebert: 28//26", "Katie Osborn: 67//67"]}'

# 2026-05-11 (5 scores)
Write-Host 'Ingesting 2026-05-11 (5 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-11", "messages": ["Gavin Speed: 5//17", "Keeran Mistry: 6//19", "Andrew Siebert: 4//13", "Katie Osborn: 5//19", "Crystal Schmidt: 10//34"]}'

# 2026-05-12 (5 scores)
Write-Host 'Ingesting 2026-05-12 (5 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-12", "messages": ["Gavin Speed: 28//6", "Keeran Mistry: 9//12", "Andrew Siebert: 5//10", "Crystal Schmidt: 19//26", "Katie Osborn: 5//9"]}'

# 2026-05-13 (4 scores)
Write-Host 'Ingesting 2026-05-13 (4 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-13", "messages": ["Keeran Mistry: 15//21", "Gavin Speed: 20//11", "Andrew Siebert: 11//11", "Katie Osborn: 18//13"]}'

# 2026-05-14 (4 scores)
Write-Host 'Ingesting 2026-05-14 (4 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-14", "messages": ["Brian Hall: 11//16", "Andrew Siebert: 13//23", "Katie Osborn: 7//30", "Crystal Schmidt: 11//26"]}'

# 2026-05-15 (5 scores)
Write-Host 'Ingesting 2026-05-15 (5 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-15", "messages": ["Gavin Speed: 27//29", "Keeran Mistry: 17//34", "Andrew Siebert: 30//25", "Brian Hall: 26//17", "Katie Osborn: 22//16"]}'

# 2026-05-16 (5 scores)
Write-Host 'Ingesting 2026-05-16 (5 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-16", "messages": ["Keeran Mistry: 15//38", "Andrew Siebert: 11//26", "Brian Hall: 10//24", "Crystal Schmidt: 27//45", "Katie Osborn: 17//32"]}'

# 2026-05-17 (4 scores)
Write-Host 'Ingesting 2026-05-17 (4 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-17", "messages": ["Andrew Siebert: 21//33", "Keeran Mistry: 25//86", "Crystal Schmidt: 47//40", "Brian Hall: 24//47"]}'

# 2026-05-18 (4 scores)
Write-Host 'Ingesting 2026-05-18 (4 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-18", "messages": ["Gavin Speed: 5//6", "Keeran Mistry: 8//13", "Andrew Siebert: 5//8", "Katie Osborn: 7//6"]}'

# 2026-05-19 (4 scores)
Write-Host 'Ingesting 2026-05-19 (4 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-19", "messages": ["Gavin Speed: 14//30", "Andrew Siebert: 8//16", "Katie Osborn: 17//25", "Crystal Schmidt: 26//28"]}'

# 2026-05-20 (7 scores)
Write-Host 'Ingesting 2026-05-20 (7 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-20", "messages": ["Andrew Osiname: 214//11", "Tim Hurley: 8//16", "Keeran Mistry: 47//20", "Crystal Schmidt: 134//41", "Andrew Siebert: 20//10", "Dorie Wallace: 29//31", "Katie Osborn: 17//8"]}'

# 2026-05-21 (8 scores)
Write-Host 'Ingesting 2026-05-21 (8 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-05-21", "messages": ["Tim Hurley: 9//33", "Keeran Mistry: 13//33", "Andrew Osiname: 191//256", "Gavin Speed: 25//19", "Dorie Wallace: 18//25", "Andrew Siebert: 12//20", "Katie Osborn: 13//27", "Brian Hall: 8//15"]}'

# Final leaderboard
Write-Host ""
Write-Host "=== FINAL LEADERBOARD ==="
Write-Host ""
Invoke-RestMethod -Uri "$base/leaderboard" | Format-Table -AutoSize