# backfill_june.ps1 - June 2026 backfill for the Zip/Patches leaderboard
# Idempotent: process() skips any date already in history (already_processed).
# Does NOT reset. Posts dates in chronological order.
#
# NOTES / data caveats:
#  * Time-style values were converted to total seconds to match the integer
#    score scale: 2:12->132, 8:34->514, 1:45->105, 1:04->64, 3:41->221,
#    2.16->136, 2.05->125, 1:50->110, 5:37->337, 1:35->95, 1:27->87.
#  * Anti-cheat floor: parse_score rejects any total < 9 (MIN_TOTAL_SCORE). This
#    drops Brian Hall's 3//4 (total 7) on 2026-06-01 and 2026-06-04, so he is
#    marked "missed" (penalty) those two days. Left as-is to respect the existing
#    anti-cheat rule. If you consider those legit, credit him AFTER running this
#    script (offsets below assume these two days were not already in history):
#      # 2026-06-01: penalty was 14//22; credit real 3//4 and clear the penalty day
#      # Invoke-RestMethod -Uri "$base/adjust?player=Brian%20Hall&add_zip=-11&add_patch=-18&add_penalties=-1" -Method POST
#      # 2026-06-04: penalty was 13//20; credit real 3//4 and clear the penalty day
#      # Invoke-RestMethod -Uri "$base/adjust?player=Brian%20Hall&add_zip=-10&add_patch=-16&add_penalties=-1" -Method POST
#
$base = "https://siebe41.synology.me"

Write-Host 'Ingesting 2026-06-01 (9 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-01", "messages": ["Gavin Speed: 5//17", "Keeran Mistry: 5//8", "Tim Hurley: 9//13", "Andrew Osiname: 13//21", "Crystal Schmidt: 6//16", "Andrew Siebert: 4//14", "Katie Osborn: 8//13", "Dorie Wallace: 12//19", "Brian Hall: 3//4"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-02 (7 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-02", "messages": ["Tim Hurley: 5//8", "Keeran Mistry: 6//14", "Andrew Siebert: 4//11", "Andrew Osiname: 11//25", "Gavin Speed: 8//15", "Dorie Wallace: 22//24", "Katie Osborn: 8//9"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-03 (6 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-03", "messages": ["Gavin Speed: 26//16", "Andrew Osiname: 337//33", "Katie Osborn: 48//28", "Dorie Wallace: 37//55", "Brian Hall: 27//20", "Crystal Schmidt: 46//34"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-04 (5 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-04", "messages": ["Andrew Siebert: 4//14", "Brian Hall: 3//4", "Katie Osborn: 8//13", "Dorie Wallace: 12//19", "Crystal Schmidt: 6//16"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-05 (8 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-05", "messages": ["Tim Hurley: 13//23", "Andrew Siebert: 12//15", "Katie Osborn: 16//48", "Dorie Wallace: 33//23", "Brian Hall: 8//15", "Gavin Speed: 15//120", "Andrew Osiname: 37//64", "Keeran Mistry: 11//45"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-06 (5 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-06", "messages": ["Tim Hurley: 16//29", "Gavin Speed: 27//47", "Andrew Osiname: 36//95", "Keeran Mistry: 26//87", "Andrew Siebert: 22//21"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-07 (9 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-07", "messages": ["Gavin Speed: 57//65", "Dorie Wallace: 91//96", "Andrew Siebert: 34//39", "Brian Hall: 14//21", "Tim Hurley: 22//19", "Keeran Mistry: 110//32", "Andrew Osiname: 337//33", "Katie Osborn: 48//28", "Crystal Schmidt: 46//34"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-08 (6 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-08", "messages": ["Andrew Siebert: 14//12", "Dorie Wallace: 18//17", "Crystal Schmidt: 12//18", "Katie Osborn: 8//41", "Tim Hurley: 7//23", "Keeran Mistry: 6//19"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-09 (6 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-09", "messages": ["Andrew Siebert: 6//16", "Dorie Wallace: 26//37", "Gavin Speed: 13//16", "Keeran Mistry: 9//11", "Tim Hurley: 8//11", "Andrew Osiname: 31//19"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-10 (6 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-10", "messages": ["Andrew Siebert: 5//23", "Dorie Wallace: 25//19", "Keeran Mistry: 13//14", "Gavin Speed: 25//11", "Tim Hurley: 6//11", "Andrew Osiname: 10//20"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-11 (6 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-11", "messages": ["Andrew Siebert: 10//23", "Dorie Wallace: 21//40", "Gavin Speed: 19//97", "Tim Hurley: 8//21", "Keeran Mistry: 9//125", "Andrew Osiname: 28//221"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-12 (5 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-12", "messages": ["Andrew Siebert: 10//21", "Keeran Mistry: 11//45", "Gavin Speed: 15//120", "Tim Hurley: 10//26", "Andrew Osiname: 37//64"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-13 (6 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-13", "messages": ["Andrew Siebert: 11//20", "Gavin Speed: 21//26", "Keeran Mistry: 48//33", "Tim Hurley: 27//35", "Dorie Wallace: 26//109", "Katie Osborn: 11//21"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-14 (8 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-14", "messages": ["Andrew Siebert: 36//21", "Katie Osborn: 43//50", "Brian Hall: 19//42", "Gavin Speed: 57//65", "Dorie Wallace: 91//96", "Tim Hurley: 27//31", "Keeran Mistry: 26//136", "Andrew Osiname: 514//105"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-15 (7 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-15", "messages": ["Andrew Siebert: 3//9", "Gavin Speed: 6//24", "Dorie Wallace: 6//21", "Katie Osborn: 6//11", "Keeran Mistry: 7//12", "Tim Hurley: 3//11", "Brian Hall: 4//14"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-16 (7 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-16", "messages": ["Andrew Siebert: 4//11", "Keeran Mistry: 6//14", "Tim Hurley: 5//8", "Andrew Osiname: 11//25", "Gavin Speed: 8//15", "Dorie Wallace: 22//24", "Katie Osborn: 8//9"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-17 (8 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-17", "messages": ["Andrew Siebert: 7//5", "Keeran Mistry: 6//6", "Tim Hurley: 9//7", "Gavin Speed: 16//5", "Andrew Osiname: 29//6", "Brian Hall: 9//25", "Dorie Wallace: 21//7", "Katie Osborn: 6//17"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-18 (8 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-18", "messages": ["Keeran Mistry: 13//55", "Gavin Speed: 22//31", "Brian Hall: 12//28", "Andrew Siebert: 8//35", "Andrew Osiname: 31//132", "Dorie Wallace: 17//34", "Madhav Patel: 75//7", "Katie Osborn: 12//35"]}' | ConvertTo-Json -Compress

Write-Host 'Ingesting 2026-06-19 (8 scores)...'
Invoke-RestMethod -Uri "$base/ingest" -Method POST -ContentType "application/json" -Body '{"date": "2026-06-19", "messages": ["Andrew Siebert: 18//30", "Gavin Speed: 16//59", "Madhav Patel: 19//60", "Keeran Mistry: 23//45", "Tim Hurley: 11//25", "Brian Hall: 35//28", "Dorie Wallace: 56//54", "Andrew Osiname: 90//87"]}' | ConvertTo-Json -Compress

Write-Host ""
Write-Host "=== LEADERBOARD ==="
Invoke-RestMethod -Uri "$base/leaderboard" | Format-Table -AutoSize
