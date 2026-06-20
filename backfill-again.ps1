# -------------------------------
# CONFIG
# -------------------------------
$endpointUrl = "https://siebe41.synology.me/ingest"
$dryRun = $false   # set to $false to actually POST

# -------------------------------
# RAW DATA (PASTE FULL DATA HERE)
# -------------------------------
$data = @"
2026-06-19,Andrew Siebert,18,30
2026-06-19,Gavin Speed,16,59
2026-06-19,Madhav Patel,19,60
2026-06-19,Keeran Mistry,23,45
2026-06-19,Tim Hurley,11,25
2026-06-19,Brian Hall,35,28
2026-06-19,Dorie Wallace,56,54
2026-06-19,Andrew Osiname,90,87

2026-06-18,Keeran Mistry,13,55
2026-06-18,Gavin Speed,22,31
2026-06-18,Brian Hall,12,28
2026-06-18,Andrew Siebert,8,35
2026-06-18,Andrew Osiname,31,2:12
2026-06-18,Dorie Wallace,17,34
2026-06-18,Madhav Patel,75,7
2026-06-18,Katie Osborn,12,35

2026-06-17,Andrew Siebert,7,5
2026-06-17,Keeran Mistry,6,6
2026-06-17,Tim Hurley,9,7
2026-06-17,Gavin Speed,16,5
2026-06-17,Andrew Osiname,29,6
2026-06-17,Brian Hall,9,25
2026-06-17,Dorie Wallace,21,7
2026-06-17,Katie Osborn,6,17

2026-06-16,Andrew Siebert,4,11
2026-06-16,Keeran Mistry,6,14
2026-06-16,Tim Hurley,5,8
2026-06-16,Andrew Osiname,11,25
2026-06-16,Gavin Speed,8,15
2026-06-16,Dorie Wallace,22,24
2026-06-16,Katie Osborn,8,9

2026-06-15,Andrew Siebert,3,9
2026-06-15,Gavin Speed,6,24
2026-06-15,Dorie Wallace,6,21
2026-06-15,Katie Osborn,6,11
2026-06-15,Keeran Mistry,7,12
2026-06-15,Tim Hurley,3,11
2026-06-15,Brian Hall,4,14

2026-06-14,Andrew Siebert,36,21
2026-06-14,Katie Osborn,43,50
2026-06-14,Brian Hall,19,42
2026-06-14,Gavin Speed,57,65
2026-06-14,Dorie Wallace,91,96
2026-06-14,Tim Hurley,27,31
2026-06-14,Keeran Mistry,26,2.16
2026-06-14,Andrew Osiname,8:34,1:45

2026-06-13,Andrew Siebert,11,20
2026-06-13,Gavin Speed,21,26
2026-06-13,Keeran Mistry,48,33
2026-06-13,Tim Hurley,27,35
2026-06-13,Dorie Wallace,26,109
2026-06-13,Katie Osborn,11,21

2026-06-12,Andrew Siebert,10,21
2026-06-12,Keeran Mistry,11,45
2026-06-12,Gavin Speed,15,120
2026-06-12,Tim Hurley,10,26
2026-06-12,Andrew Osiname,37,1:04

2026-06-11,Andrew Siebert,10,23
2026-06-11,Dorie Wallace,21,40
2026-06-11,Gavin Speed,19,97
2026-06-11,Tim Hurley,8,21
2026-06-11,Keeran Mistry,9,2.05
2026-06-11,Andrew Osiname,28,3:41

2026-06-10,Andrew Siebert,5,23
2026-06-10,Dorie Wallace,25,19
2026-06-10,Keeran Mistry,13,14
2026-06-10,Gavin Speed,25,11
2026-06-10,Tim Hurley,6,11
2026-06-10,Andrew Osiname,10,20

2026-06-09,Andrew Siebert,6,16
2026-06-09,Dorie Wallace,26,37
2026-06-09,Gavin Speed,13,16
2026-06-09,Keeran Mistry,9,11
2026-06-09,Tim Hurley,8,11
2026-06-09,Andrew Osiname,31,19

2026-06-08,Andrew Siebert,14,12
2026-06-08,Dorie Wallace,18,17
2026-06-08,Crystal Schmidt,12,18
2026-06-08,Katie Osborn,8,41
2026-06-08,Tim Hurley,7,23
2026-06-08,Keeran Mistry,6,19

2026-06-07,Gavin Speed,57,65
2026-06-07,Dorie Wallace,91,96
2026-06-07,Andrew Siebert,34,39
2026-06-07,Brian Hall,14,21
2026-06-07,Tim Hurley,22,19
2026-06-07,Keeran Mistry,1:50,32
2026-06-07,Andrew Osiname,5:37,33
2026-06-07,Katie Osborn,48,28
2026-06-07,Crystal Schmidt,46,34

2026-06-06,Tim Hurley,16,29
2026-06-06,Gavin Speed,27,47
2026-06-06,Andrew Osiname,36,1:35
2026-06-06,Keeran Mistry,26,1:27
2026-06-06,Andrew Siebert,22,21

2026-06-05,Tim Hurley,13,23
2026-06-05,Andrew Siebert,12,15
2026-06-05,Katie Osborn,16,48
2026-06-05,Dorie Wallace,33,23
2026-06-05,Brian Hall,8,15
2026-06-05,Gavin Speed,15,120
2026-06-05,Andrew Osiname,37,1:04
2026-06-05,Keeran Mistry,11,45
"@ -split "`n"

# -------------------------------
# TIME NORMALIZATION FUNCTION
# -------------------------------
function Convert-ToSeconds($value) {
    $value = $value.Trim()

    # mm:ss
    if ($value -match "^\d+:\d+$") {
        $p = $value.Split(":")
        return ([int]$p[0] * 60) + [int]$p[1]
    }

    # shorthand (2.1 = 2:10)
    if ($value -match "^\d+\.\d+$") {
        $p = $value.Split(".")
        return ([int]$p[0] * 60) + ([int]$p[1] * 10)
    }

    # plain int
    if ($value -match "^\d+$") {
        return [int]$value
    }

    return $value
}

# -------------------------------
# PARSE DATA
# -------------------------------
$parsed = $data |
Where-Object { $_.Trim() -ne "" } |
ForEach-Object {
    $parts = $_.Split(",")

    [PSCustomObject]@{
        Date    = $parts[0].Trim()
        Name    = $parts[1].Trim()
        Zip     = Convert-ToSeconds $parts[2]
        Patches = Convert-ToSeconds $parts[3]
    }
}

# -------------------------------
# GROUP + BUILD PAYLOADS
# -------------------------------
$grouped = $parsed | Group-Object Date

foreach ($group in $grouped) {
    $date = $group.Name

    $messages = $group.Group | ForEach-Object {
        "$($_.Name): $($_.Zip)//$($_.Patches)"
    }

    $payload = @{
        date     = $date
        messages = $messages
    } | ConvertTo-Json -Depth 3

    Write-Host "------------------------"
    Write-Host "Payload for ${date}:"
    Write-Host $payload

    if (-not $dryRun) {
        Invoke-RestMethod -Uri $endpointUrl -Method Post -Body $payload -ContentType "application/json"
    }
}