<#
.SYNOPSIS
Run the whole RONB Clips test suite against a throwaway local PostgreSQL 16 cluster.

.DESCRIPTION
No admin rights, Docker, psql or PostgreSQL install are needed. The script downloads
the official EDB Windows x64 "binaries" zip (SHA-256 checked), starts a temporary
cluster on a spare port, creates the test role and database, runs tests/run.py with
RONBCLIPS_TEST_DATABASE_URL, then stops the cluster and deletes its data. The zip is
kept under the temp folder so later runs are fast. Only PowerShell built-ins are
used; Python dependencies are the ones already in requirements.txt.

USAGE
  powershell -ExecutionPolicy Bypass -File scripts\run-postgres-tests.ps1
  powershell -ExecutionPolicy Bypass -File scripts\run-postgres-tests.ps1 -Port 55432

Exit code is the test suite's exit code (0 only when it prints "All tests passed.").
#>
[CmdletBinding()]
param([int]$Port = 55432)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Temp = Join-Path $env:TEMP 'opencode\ronbclips-pg16'
$Url = 'https://get.enterprisedb.com/postgresql/postgresql-16.16-1-windows-x64-binaries.zip'
$Sha = '5C0D06F3D26A929ECB36DD931409F1E51A4181355794A259B90AAE8BFFC57A9F'
$Zip = Join-Path $Temp 'postgresql-16.16-1-windows-x64-binaries.zip'
$Bin = Join-Path $Temp 'pgsql\bin'
$Data = Join-Path $Temp 'cluster-data'
$PgLog = Join-Path $Temp 'postgres.log'
$Result = 1

New-Item -ItemType Directory -Force -Path $Temp | Out-Null

# 1. Obtain the portable binaries once and verify them against EDB's published hash.
if (-not (Test-Path -LiteralPath $Zip) -or (Get-FileHash -Algorithm SHA256 -LiteralPath $Zip).Hash -ne $Sha) {
    Write-Host "Downloading PostgreSQL 16 binaries from $Url ..."
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $Url -OutFile $Zip -TimeoutSec 600
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Zip).Hash -ne $Sha) {
        throw 'The downloaded PostgreSQL archive failed the SHA-256 check.'
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $Bin 'initdb.exe'))) {
    Write-Host 'Unpacking PostgreSQL ...'
    Expand-Archive -LiteralPath $Zip -DestinationPath $Temp -Force
}
$initdb = Join-Path $Bin 'initdb.exe'
$pgctl = Join-Path $Bin 'pg_ctl.exe'
$psql = Join-Path $Bin 'psql.exe'
$pgdump = Join-Path $Bin 'pg_dump.exe'

try {
    # 2. Fresh throwaway cluster on a spare port, owned by a throwaway role/database.
    if (Test-Path -LiteralPath (Join-Path $Data 'postmaster.pid')) {
        & $pgctl -D $Data -m immediate -w stop *> $null
    }
    Remove-Item -LiteralPath $Data -Recurse -Force -ErrorAction SilentlyContinue
    & $initdb -D $Data -U postgres --auth=trust --encoding=UTF8 --no-instructions
    if ($LASTEXITCODE -ne 0) { throw 'initdb failed.' }
    & $pgctl -D $Data -l $PgLog -o "-p $Port -c listen_addresses=127.0.0.1" -w start
    if ($LASTEXITCODE -ne 0) { throw 'pg_ctl could not start PostgreSQL.' }
    for ($i = 0; $i -lt 30; $i++) {
        $null = & $psql -h 127.0.0.1 -p $Port -U postgres -d postgres -c 'select 1' 2>&1
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 1
    }
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL did not answer on port $Port." }
    & $psql -h 127.0.0.1 -p $Port -U postgres -d postgres -v ON_ERROR_STOP=1 `
        -c "CREATE ROLE ronbclips LOGIN PASSWORD 'testpassword';" `
        -c 'CREATE DATABASE ronbclips_test OWNER ronbclips;' `
        -c 'CREATE DATABASE ronbclips_backup_source OWNER ronbclips;' `
        -c 'CREATE DATABASE ronbclips_backup_target OWNER ronbclips;'
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the test role and databases.' }

    # 3. Run the exact suite the project uses, pointed at PostgreSQL. The backup test gets its own two databases so a
    #    real dump and restore can never touch the shared schema used by the other suites.
    $env:RONBCLIPS_TEST_DATABASE_URL = "postgresql+psycopg://ronbclips:testpassword@127.0.0.1:$Port/ronbclips_test"
    $env:RONBCLIPS_TEST_BACKUP_SOURCE_URL = "postgresql+psycopg://ronbclips:testpassword@127.0.0.1:$Port/ronbclips_backup_source"
    $env:RONBCLIPS_TEST_BACKUP_TARGET_URL = "postgresql+psycopg://ronbclips:testpassword@127.0.0.1:$Port/ronbclips_backup_target"
    $env:RONBCLIPS_TEST_PG_ADMIN_URL = "postgresql+psycopg://postgres@127.0.0.1:$Port/postgres"
    $env:RONBCLIPS_TEST_PG_BIN = $Bin
    if (-not (Test-Path -LiteralPath $pgdump)) { throw "pg_dump.exe is missing from $Bin" }
    $env:RONBCLIPS_COVERAGE = '0'
    Push-Location $Root
    try {
        & python tests/run.py
        $Result = $LASTEXITCODE
    }
    finally { Pop-Location }
}
finally {
    # 4. Never leave the cluster or its data behind, even if the tests fail.
    if (Test-Path -LiteralPath (Join-Path $Data 'postmaster.pid')) {
        & $pgctl -D $Data -m fast -w stop
    }
    Remove-Item -LiteralPath $Data -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PgLog -Force -ErrorAction SilentlyContinue
}
exit $Result
