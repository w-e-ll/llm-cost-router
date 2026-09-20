param(
    [ValidateSet('Setup','Up','MigrateNative','CompleteMigration','Verify','RemoveNative','Test','Logs','Down')]
    [string]$Action = 'Up'
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerExe = if ($dockerCommand) { $dockerCommand.Source } else { 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' }
$dockerBin = Split-Path -Parent $dockerExe
# Docker Desktop may be installed correctly while its credential helper is not
# on PATH after an upgrade. Add the installed helper for this process only.
$credentialHelper = Join-Path $dockerBin 'docker-credential-desktop.exe'
if ((Test-Path -LiteralPath $credentialHelper) -and -not (Get-Command docker-credential-desktop -ErrorAction SilentlyContinue)) {
    $env:Path = "$dockerBin;$env:Path"
}
$nativeRoot = Join-Path $projectRoot 'var\postgres'
$backupRoot = Join-Path $projectRoot 'var\backups\native-migration'
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Docker {
    & $dockerExe @args
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed (exit $LASTEXITCODE). No native data has been deleted." }
}
function Compose { Docker compose @args }
function Secret {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
}
function Setup {
    New-Item -ItemType Directory -Path (Join-Path $projectRoot 'var\logs') -Force | Out-Null
    $envPath = Join-Path $projectRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath)) {
        Copy-Item -LiteralPath (Join-Path $projectRoot '.env.example') -Destination $envPath
    }
    $content = [IO.File]::ReadAllText($envPath)
    foreach ($name in @('POSTGRES_ADMIN_PASSWORD','POSTGRES_APP_PASSWORD')) {
        $pattern = '(?m)^' + $name + '=([^\r\n]*)'
        $match = [regex]::Match($content, $pattern)
        if (-not $match.Success) { $content += "`n$name=$(Secret)`n" }
        elseif ($match.Groups[1].Value -in @('', 'CHANGE_ME')) {
            $content = [regex]::Replace($content, $pattern, "$name=$(Secret)")
        }
        elseif ($match.Groups[1].Value -notmatch '^[a-zA-Z0-9_-]{24,}$') {
            throw "$name must be an unquoted URL-safe password of at least 24 characters."
        }
    }
    [IO.File]::WriteAllText($envPath, $content, $utf8)
    Write-Host 'Docker credentials configured; existing OpenAI key preserved.'
}
function NativeTool {
    param([string]$Tool, [string[]]$Arguments)
    & (Join-Path $nativeRoot "runtime\pgsql\bin\$Tool.exe") @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Native PostgreSQL $Tool failed (exit $LASTEXITCODE)." }
}
# Compare every persisted row, including IDs, config, payload and timestamps.
$fingerprintSql = @'
SET timezone='UTC';
SELECT json_build_object(
 'runs', (SELECT count(*) FROM experiment_runs),
 'run_hash', (SELECT md5(coalesce(string_agg(row_to_json(r)::text, '' ORDER BY r.id),'')) FROM experiment_runs r),
 'plans', (SELECT count(*) FROM cost_plans),
 'plan_hash', (SELECT md5(coalesce(string_agg(row_to_json(p)::text, '' ORDER BY p.id),'')) FROM cost_plans p)
)::text;
'@
function TargetFingerprint {
    $lines = Compose exec -T db psql -X -qAt -v ON_ERROR_STOP=1 -U llm_router -d llm_cost_router -c $fingerprintSql
    return ($lines | Where-Object { $_ -match '^\{' } | Select-Object -Last 1)
}
function Verify {
    $apiPort = if ($env:API_PORT) { $env:API_PORT } else { '8000' }
    $uiPort = if ($env:UI_PORT) { $env:UI_PORT } else { '8501' }
    # Compose settings also support port overrides in .env.
    foreach ($line in [IO.File]::ReadAllLines((Join-Path $projectRoot '.env'))) {
        if ($line -match '^API_PORT=(\d+)$') { $apiPort = $Matches[1] }
        if ($line -match '^UI_PORT=(\d+)$') { $uiPort = $Matches[1] }
    }
    $health = Invoke-RestMethod "http://127.0.0.1:$apiPort/health/db"
    if ($health.status -ne 'ok') { throw 'Database health check failed.' }
    $null = Invoke-WebRequest "http://127.0.0.1:$uiPort/_stcore/health" -UseBasicParsing
    Compose ps
    Write-Host 'API, PostgreSQL and Streamlit health checks passed. No model calls made.'
}

function CompleteMigration {
    $marker = Get-Content -LiteralPath (Join-Path $backupRoot 'restored.json') -Raw | ConvertFrom-Json
    if ((Get-FileHash -LiteralPath $marker.dump -Algorithm SHA256).Hash -ne $marker.sha256) { throw 'Backup checksum mismatch.' }
    Compose up -d --wait db
    if ((TargetFingerprint) -ne $marker.fingerprint) { throw 'Restored data differs. Native files retained.' }
    & (Join-Path $nativeRoot 'runtime\pgsql\bin\pg_ctl.exe') -D (Join-Path $nativeRoot 'data') status
    if ($LASTEXITCODE -eq 0) {
        NativeTool pg_ctl @('-D',(Join-Path $nativeRoot 'data'),'-m','fast','-w','stop')
    } elseif ($LASTEXITCODE -ne 3) { throw 'Cannot verify native server status.' }
    Compose up -d --build --wait --wait-timeout 180
    Verify
    Compose restart db
    Compose up -d --wait --wait-timeout 180
    Verify
    if ((TargetFingerprint) -ne $marker.fingerprint) { throw 'Post-restart verification failed.' }
    [IO.File]::WriteAllText((Join-Path $backupRoot 'verified.json'), ($marker | ConvertTo-Json), $utf8)
    Write-Host 'Migration and restart verified. Native server stopped. Run RemoveNative to remove native files and retain the backup.'
}

switch ($Action) {
    Setup { Setup }
    Up {
        Setup
        Compose up -d --build --wait --wait-timeout 180
        Verify
    }
    MigrateNative {
        Setup
        Docker info --format '{{.ServerVersion}}'
        if (Test-Path (Join-Path $backupRoot 'verified.json')) {
            throw 'Migration already verified. Use Up, Verify or RemoveNative.'
        }
        if (Test-Path (Join-Path $backupRoot 'restored.json')) {
            CompleteMigration
            break
        }
        $credentials = Get-Content -LiteralPath (Join-Path $nativeRoot 'bootstrap.json') -Raw | ConvertFrom-Json
        $previousPassword = $env:PGPASSWORD
        $env:PGPASSWORD = $credentials.app
        try {
            $clients = NativeTool psql @('-X','-qAt','-h','127.0.0.1','-p','5433','-U','llm_router','-d','llm_cost_router','-c',"SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend'")
            if ([int]($clients | Select-Object -Last 1) -ne 0) { throw 'Stop the PyCharm API/UI and close database connections before migrating.' }
            $started = NativeTool psql @('-X','-qAt','-h','127.0.0.1','-p','5433','-U','llm_router','-d','llm_cost_router','-c',"SELECT count(*) FROM experiment_runs WHERE status='started'")
            if ([int]($started | Select-Object -Last 1) -ne 0) { throw 'Resolve started experiment records before migration; do not retry paid runs blindly.' }
            New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
            $before = NativeTool psql @('-X','-qAt','-h','127.0.0.1','-p','5433','-U','llm_router','-d','llm_cost_router','-c',$fingerprintSql)
            $before = ($before | Where-Object { $_ -match '^\{' } | Select-Object -Last 1)
            $dumpPath = Join-Path $backupRoot ('database-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.dump')
            NativeTool pg_dump @('-h','127.0.0.1','-p','5433','-U','llm_router','-d','llm_cost_router','-Fc','--no-owner','--no-acl','-f',$dumpPath)
            $after = NativeTool psql @('-X','-qAt','-h','127.0.0.1','-p','5433','-U','llm_router','-d','llm_cost_router','-c',$fingerprintSql)
            $after = ($after | Where-Object { $_ -match '^\{' } | Select-Object -Last 1)
            if (-not $before -or $before -ne $after) { throw 'Native data changed during backup. Stop writers and retry.' }
            [IO.File]::WriteAllText((Join-Path $backupRoot 'fingerprint.json'), $before, $utf8)
            Compose stop api ui
            Compose up -d --wait db
            $tables = Compose exec -T db psql -X -qAt -U llm_router -d llm_cost_router -c "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
            $rows = Compose exec -T db psql -X -qAt -U llm_router -d llm_cost_router -c "SELECT (SELECT count(*) FROM experiment_runs) + (SELECT count(*) FROM cost_plans)"
            if ([int]($tables | Select-Object -Last 1) -eq 0) {
                Compose cp $dumpPath 'db:/tmp/router-native.dump'
                Compose exec -T db pg_restore --exit-on-error --single-transaction --no-owner --no-acl -U llm_router -d llm_cost_router /tmp/router-native.dump
            } elseif ([int]($rows | Select-Object -Last 1) -eq 0) {
                Compose cp $dumpPath 'db:/tmp/router-native.dump'
                $targetRuns = Compose exec -T db psql -X -qAt -U llm_router -d llm_cost_router -c "SELECT count(*) FROM experiment_runs"
                if ([int]($targetRuns | Select-Object -Last 1) -eq 0) {
                    Compose exec -T db pg_restore --data-only --table=experiment_runs --exit-on-error --single-transaction --no-owner --no-acl -U llm_router -d llm_cost_router /tmp/router-native.dump
                }
                $sourceRun = Compose exec -T db psql -X -qAt -U llm_router -d llm_cost_router -c "SELECT count(*) FROM experiment_runs"
                if ([int]($sourceRun | Select-Object -Last 1) -eq 0) { throw 'No experiment rows were restored; dependent plans were not attempted.' }
                $targetPlans = Compose exec -T db psql -X -qAt -U llm_router -d llm_cost_router -c "SELECT count(*) FROM cost_plans"
                if ([int]($targetPlans | Select-Object -Last 1) -eq 0) {
                    Compose exec -T db pg_restore --data-only --table=cost_plans --exit-on-error --single-transaction --no-owner --no-acl -U llm_router -d llm_cost_router /tmp/router-native.dump
                }
                Write-Host 'Docker schema was already migrated; restored experiment rows before dependent cost plans.'
            } else {
                $targetBeforeRestore = TargetFingerprint
                if ($targetBeforeRestore -ne $before) {
                    throw 'Docker database is not empty and its fingerprint differs from native PostgreSQL. Refusing to overwrite it. Backup is safe; native database remains installed.'
                }
                Write-Host 'Docker database already matches native PostgreSQL; adopting it without overwrite.'
            }
            $target = TargetFingerprint
            if ($target -ne $before) { throw 'Docker data fingerprint differs after migration. Native PostgreSQL retained.' }
            $marker = @{ dump = $dumpPath; sha256 = (Get-FileHash -LiteralPath $dumpPath -Algorithm SHA256).Hash; fingerprint = $before }
            [IO.File]::WriteAllText((Join-Path $backupRoot 'restored.json'), ($marker | ConvertTo-Json), $utf8)
            CompleteMigration
        } finally { $env:PGPASSWORD = $previousPassword }
    }
    CompleteMigration { CompleteMigration }
    RemoveNative {
        $marker = Get-Content -LiteralPath (Join-Path $backupRoot 'verified.json') -Raw | ConvertFrom-Json
        if ((Get-FileHash -LiteralPath $marker.dump -Algorithm SHA256).Hash -ne $marker.sha256) { throw 'Backup checksum mismatch.' }
        Verify
        if ((TargetFingerprint) -ne $marker.fingerprint) { throw 'Docker data changed since migration. Reconcile the backup before removal.' }
        $nativeData = Join-Path $nativeRoot 'data'
        if (Test-Path -LiteralPath (Join-Path $nativeData 'PG_VERSION')) {
            & (Join-Path $nativeRoot 'runtime\pgsql\bin\pg_ctl.exe') -D $nativeData status
            if ($LASTEXITCODE -notin @(0, 3)) { throw 'Unable to determine native server status. Refusing removal.' }
            if ($LASTEXITCODE -eq 0) { throw 'Native server is still running. Refusing removal.' }
        } else {
            Write-Host 'Native data directory is already stopped/partial; verified Docker data permits removal.'
        }
        $resolved = (Resolve-Path -LiteralPath $nativeRoot).Path
        $expected = [IO.Path]::GetFullPath((Join-Path $projectRoot 'var\postgres'))
        if ($resolved -ne $expected -or -not $resolved.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar)) { throw 'Unsafe removal path.' }
        if ((Get-Item -LiteralPath (Join-Path $projectRoot 'var')).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'var is a link; refusing removal.' }
        if ((Get-Item -LiteralPath $resolved).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Native directory is a link; refusing removal.' }
        if (Get-ChildItem -LiteralPath $resolved -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) { throw 'Reparse points found; refusing recursive removal.' }
        Remove-Item -LiteralPath $resolved -Recurse -Force
        $envPath = Join-Path $projectRoot '.env'
        $content = [IO.File]::ReadAllText($envPath)
        $password = [regex]::Match($content, '(?m)^POSTGRES_APP_PASSWORD=([^\r\n]+)').Groups[1].Value
        $portMatch = [regex]::Match($content, '(?m)^POSTGRES_PORT=(\d+)')
        $port = if ($env:POSTGRES_PORT) { $env:POSTGRES_PORT } elseif ($portMatch.Success) { $portMatch.Groups[1].Value } else { '5434' }
        foreach ($entry in @(@('DATABASE_URL','llm_cost_router'), @('TEST_DATABASE_URL','llm_cost_router_test'))) {
            $newLine = $entry[0] + '=postgresql+psycopg://llm_router:' + $password + '@127.0.0.1:' + $port + '/' + $entry[1]
            $pattern = '(?m)^' + $entry[0] + '=[^\r\n]*'
            if ([regex]::IsMatch($content, $pattern)) { $content = [regex]::Replace($content, $pattern, $newLine) }
            else { $content += "`n$newLine`n" }
        }
        [IO.File]::WriteAllText($envPath, $content, $utf8)
        Write-Host 'Project-local native PostgreSQL removed. Docker data and verified backup retained.'
    }
    Verify { Verify }
    Test {
        Compose up -d --wait db
        Compose --profile test build tests
        Compose --profile test run --rm tests uv run --no-sync pytest -q -p no:cacheprovider
        Compose --profile test run --rm tests uv run --no-sync ruff check --no-cache .
    }
    Logs { Compose logs --follow --tail 100 api ui db }
    Down { Compose down }
}
