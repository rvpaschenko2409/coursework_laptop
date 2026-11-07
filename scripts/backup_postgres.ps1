$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

$backupDir = Join-Path ([Environment]::GetFolderPath('Desktop')) 'coursework_backups'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss_ffff'
$remote = "/tmp/coursework_laptop_$stamp.dump"
$archive = Join-Path $backupDir "coursework_laptop_$stamp.dump"
$partial = "$archive.partial"

try {
    docker compose exec -T db pg_dump -U postgres -d coursework_laptop -Fc -f $remote
    if ($LASTEXITCODE -ne 0) { throw 'pg_dump завершился ошибкой.' }

    docker compose exec -T db pg_restore --list $remote | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось прочитать оглавление архива.' }

    docker compose cp "db:$remote" $partial
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось скопировать архив из Docker.' }

    Move-Item -LiteralPath $partial -Destination $archive
    Get-Item -LiteralPath $archive | Select-Object Name, Length
} finally {
    Remove-Item -LiteralPath $partial -ErrorAction SilentlyContinue
    docker compose exec -T db rm -f $remote | Out-Null
}
