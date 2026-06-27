# backfill_june.ps1 - June 2026 backfill for the Zip/Patches leaderboard
# Idempotent: process() skips any date already in history (already_processed).
# Does NOT reset. Posts dates in chronological order.
$base = "https://siebe41.synology.me"

Write-Host 'Ingesting 2026-06-01 (9 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-01", "messages": ["Gavin Speed: 5//17", "Keeran Mistry: 5//8", "Tim Hurley: 9//13", "Andrew Osiname: 13//21", "Crystal Schmidt: 6//16", "Andrew Siebert: 4//14", "Brian Hall: 3//4", "Katie Osborn: 8//13", "Dorie Wallace: 12//19"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-02 (7 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-02", "messages": ["Andrew Osiname: 13//17", "Gavin Speed: 5//20", "Tim Hurley: 6//24", "Keeran Mistry: 6//11", "Andrew Siebert: 6//12", "Brian Hall: 6//12", "Katie Osborn: 5//21"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-03 (8 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-03", "messages": ["Keeran Mistry: 10//14", "Brian Hall: 8//10", "Andrew Siebert: 9//12", "Katie Osborn: 17//15", "Andrew Osiname: 21//24", "Gavin Speed: 12//10", "Tim Hurley: 8//15", "Crystal Schmidt: 19//25"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-04 (8 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-04", "messages": ["Katie Osborn: 16//12", "Andrew Siebert: 8//13", "Tim Hurley: 10//20", "Andrew Osiname: 54//111", "Crystal Schmidt: 16//29", "Brian Hall: 1//1", "Gavin Speed: 22//40", "Keeran Mistry: 12//34"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-05 (8 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-05", "messages": ["Tim Hurley: 13//23", "Andrew Siebert: 12//15", "Katie Osborn: 16//48", "Dorie Wallace: 33//23", "Brian Hall: 8//15", "Gavin Speed: 25//32", "Keeran Mistry: 12//26", "Andrew Osiname: 24//314"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-06 (9 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-06", "messages": ["Tim Hurley: 16//29", "Gavin Speed: 27//47", "Andrew Osiname: 36//95", "Keeran Mistry: 26//87", "Andrew Siebert: 22//21", "Brian Hall: 14//21", "Dorie Wallace: 28//31", "Crystal Schmidt: 33//44", "Katie Osborn: 19//33"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-07 (9 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-07", "messages": ["Gavin Speed: 26//16", "Andrew Osiname: 337//33", "Katie Osborn: 48//28", "Dorie Wallace: 37//55", "Brian Hall: 27//20", "Crystal Schmidt: 46//34", "Keeran Mistry: 110//32", "Tim Hurley: 22//19", "Andrew Siebert: 34//39"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-08 (8 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-08", "messages": ["Dorie Wallace: 18//17", "Katie Osborn: 8//41", "Crystal Schmidt: 12//18", "Andrew Siebert: 14//12", "Tim Hurley: 7//23", "Keeran Mistry: 6//19", "Gavin Speed: 16//9", "Andrew Osiname: 12//13"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-09 (9 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-09", "messages": ["Keeran Mistry: 9//11", "Tim Hurley: 8//11", "Andrew Osiname: 31//19", "Gavin Speed: 13//16", "Dorie Wallace: 26//37", "Andrew Siebert: 6//16", "Katie Osborn: 9//22", "Brian Hall: 9//14", "Crystal Schmidt: 16//51"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-10 (8 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-10", "messages": ["Gavin Speed: 25//11", "Tim Hurley: 6//11", "Andrew Osiname: 10//20", "Keeran Mistry: 13//14", "Dorie Wallace: 25//19", "Brian Hall: 11//18", "Andrew Siebert: 5//23", "Katie Osborn: 29//8"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-11 (6 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-11", "messages": ["Andrew Siebert: 10//23", "Dorie Wallace: 21//40", "Gavin Speed: 19//97", "Tim Hurley: 8//21", "Keeran Mistry: 9//125", "Andrew Osiname: 28//221"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-12 (5 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-12", "messages": ["Andrew Siebert: 10//21", "Keeran Mistry: 11//45", "Gavin Speed: 15//120", "Tim Hurley: 10//26", "Andrew Osiname: 37//64"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-13 (6 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-13", "messages": ["Andrew Siebert: 11//20", "Gavin Speed: 21//26", "Keeran Mistry: 48//33", "Tim Hurley: 27//35", "Dorie Wallace: 26//109", "Katie Osborn: 11//21"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-14 (7 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-14", "messages": ["Andrew Siebert: 36//21", "Katie Osborn: 43//50", "Gavin Speed: 57//65", "Dorie Wallace: 91//96", "Tim Hurley: 27//31", "Keeran Mistry: 26//136", "Andrew Osiname: 514//105"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-15 (7 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-15", "messages": ["Andrew Siebert: 3//9", "Gavin Speed: 6//24", "Dorie Wallace: 6//21", "Katie Osborn: 6//11", "Keeran Mistry: 7//12", "Tim Hurley: 3//11", "Brian Hall: 4//14"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-16 (7 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-16", "messages": ["Andrew Siebert: 4//11", "Keeran Mistry: 6//14", "Tim Hurley: 5//8", "Andrew Osiname: 11//25", "Gavin Speed: 8//15", "Dorie Wallace: 22//24", "Katie Osborn: 8//9"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-17 (6 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-17", "messages": ["Andrew Siebert: 7//5", "Keeran Mistry: 6//6", "Tim Hurley: 9//7", "Gavin Speed: 16//5", "Andrew Osiname: 29//6", "Brian Hall: 9//25"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-18 (9 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-18", "messages": ["Andrew Siebert: 8//35", "Keeran Mistry: 13//55", "Gavin Speed: 22//31", "Brian Hall: 12//28", "Andrew Osiname: 31//132", "Dorie Wallace: 17//34", "Madhav Patel: 75//7", "Katie Osborn: 12//35", "Tim Hurley: 12//21"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-19 (8 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-19", "messages": ["Andrew Siebert: 18//30", "Gavin Speed: 16//59", "Madhav Patel: 19//60", "Keeran Mistry: 23//45", "Tim Hurley: 11//25", "Brian Hall: 35//28", "Dorie Wallace: 56//54", "Andrew Osiname: 90//87"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-20 (9 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-20", "messages": ["Andrew Siebert: 33//34", "Tim Hurley: 22//45", "Gavin Speed: 33//48", "Keeran Mistry: 19//28", "Andrew Osiname: 33//69", "Dorie Wallace: 49//53", "Katie Osborn: 9//27", "Crystal Schmidt: 43//40", "Marie Chalfant: 37//7"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-21 (8 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-21", "messages": ["Andrew Siebert: 26//37", "Tim Hurley: 19//31", "Gavin Speed: 31//49", "Keeran Mistry: 18//53", "Andrew Osiname: 105//35", "Dorie Wallace: 61//66", "Crystal Schmidt: 37//113", "Katie Osborn: 20//40"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-22 (9 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-22", "messages": ["Andrew Siebert: 7//6", "Brian Hall: 8//8", "Gavin Speed: 11//4", "Tim Hurley: 10//5", "Keeran Mistry: 9//9", "Andrew Osiname: 16//13", "Dorie Wallace: 14//16", "Crystal Schmidt: 8//14", "Katie Osborn: 8//6"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-23 (9 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-23", "messages": ["Andrew Siebert: 6//9", "Katie Osborn: 12//14", "Andrew Osiname: 15//25", "Dorie Wallace: 11//22", "Crystal Schmidt: 23//18", "Madhav Patel: 37//13", "Gavin Speed: 6//14", "Tim Hurley: 8//11", "Keeran Mistry: 10//13"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-24 (9 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-24", "messages": ["Andrew Siebert: 12//18", "Brian Hall: 17//16", "Dorie Wallace: 23//40", "Andrew Osiname: 14//15", "Keeran Mistry: 13//33", "Gavin Speed: 18//30", "Tim Hurley: 20//38", "Crystal Schmidt: 24//21", "Madhav Patel: 31//40"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-25 (8 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-25", "messages": ["Andrew Siebert: 16//31", "Katie Osborn: 16//57", "Crystal Schmidt: 42//105", "Dorie Wallace: 20//33", "Gavin Speed: 38//32", "Tim Hurley: 17//25", "Andrew Osiname: 96//47", "Keeran Mistry: 20//23"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-26 (8 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-26", "messages": ["Andrew Siebert: 12//29", "Katie Osborn: 23//27", "Gavin Speed: 18//29", "Keeran Mistry: 21//17", "Andrew Osiname: 25//215", "Tim Hurley: 15//100", "Crystal Schmidt: 42//100", "Dorie Wallace: 93//27"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-27 (9 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-27", "messages": ["Andrew Siebert: 19//39", "Andrew Osiname: 36//43", "Brian Hall: 17//23", "Keeran Mistry: 28//41", "Katie Osborn: 25//58", "Crystal Schmidt: 42//28", "Gavin Speed: 47//54", "Tim Hurley: 21//105", "Marie Chalfant: 146//20"]}' | ConvertTo-Json -Compress

Write-Host ""
Write-Host "=== LEADERBOARD ==="
Invoke-RestMethod -Uri "$base/leaderboard" | Format-Table -AutoSize
