param(
    [Parameter(Mandatory = $true)]
    [string]$Pack,

    [switch]$Build,

    # Copy only Pathumwan/data/Dataset.csv (do not touch derived files)
    [switch]$DatasetOnly
)

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$packsRoot = Resolve-Path $PSScriptRoot

$packRoot = Join-Path $packsRoot $Pack

$sourceDataset = Join-Path $packRoot "Pathumwan" "data" "Dataset.csv"
if (-not (Test-Path $sourceDataset)) {
    Write-Error "Pack not found or missing Dataset.csv: $sourceDataset"
    Write-Host "Available packs:" -ForegroundColor Yellow
    Get-ChildItem -Path $packsRoot -Directory | Select-Object -ExpandProperty Name | Sort-Object | ForEach-Object { Write-Host "  - $_" }
    exit 1
}

$destination = Join-Path $repoRoot "Pathumwan" "data" "Dataset.csv"
if (-not (Test-Path (Split-Path -Parent $destination))) {
    Write-Error "Destination folder not found: $(Split-Path -Parent $destination)"
    exit 1
}

Copy-Item -Path $sourceDataset -Destination $destination -Force
Write-Host "Copied: $sourceDataset" -ForegroundColor Green
Write-Host "   -> $destination" -ForegroundColor Green

$pathumwanRoot = Join-Path $repoRoot "Pathumwan"

# Copy derived SUMO files if present (so SUMO changes immediately without rebuild)
if (-not $DatasetOnly) {
    $derivedPairs = @(
        @{
            Source = (Join-Path $packRoot "Pathumwan" "osm.dataset.trips.xml")
            Dest   = (Join-Path $pathumwanRoot "osm.dataset.trips.xml")
        },
        @{
            Source = (Join-Path $packRoot "Pathumwan" "osm.dataset.rou.xml")
            Dest   = (Join-Path $pathumwanRoot "osm.dataset.rou.xml")
        },
        @{
            Source = (Join-Path $packRoot "Pathumwan" "data" "dataset_route_mapping.generated.json")
            Dest   = (Join-Path $pathumwanRoot "data" "dataset_route_mapping.generated.json")
        }
    )

    $copiedAny = $false
    foreach ($pair in $derivedPairs) {
        if (Test-Path $pair.Source) {
            Copy-Item -Path $pair.Source -Destination $pair.Dest -Force
            Write-Host "Copied: $($pair.Source)" -ForegroundColor Green
            Write-Host "   -> $($pair.Dest)" -ForegroundColor Green
            $copiedAny = $true
        }
    }

    if (-not $copiedAny) {
        Write-Warning "This pack does not include derived SUMO files yet. Use -Build to regenerate routes, or re-generate pack outputs first."
    }
}

if ($Build) {
    $generator = Join-Path $pathumwanRoot "generate_dataset_routes.py"
    $netFile = Join-Path $pathumwanRoot "osm.net.xml"
    $outTrips = Join-Path $pathumwanRoot "osm.dataset.trips.xml"
    $outRou = Join-Path $pathumwanRoot "osm.dataset.rou.xml"
    $outMapping = Join-Path $pathumwanRoot "data" "dataset_route_mapping.generated.json"

    if (-not (Test-Path $generator)) {
        Write-Error "generate_dataset_routes.py not found: $generator"
        exit 1
    }
    if (-not (Test-Path $netFile)) {
        Write-Error "osm.net.xml not found: $netFile"
        exit 1
    }

    $pythonCmd = (Get-Command python -ErrorAction SilentlyContinue)
    if (-not $pythonCmd) {
        $pythonCmd = (Get-Command py -ErrorAction SilentlyContinue)
    }
    if (-not $pythonCmd) {
        Write-Error "Python not found (python/py)."
        exit 1
    }

    $duarouter = $null
    if ($env:SUMO_HOME) {
        $candidate = Join-Path $env:SUMO_HOME "bin" "duarouter.exe"
        if (Test-Path $candidate) {
            $duarouter = $candidate
        }
    }
    if (-not $duarouter) {
        $cmd = (Get-Command duarouter -ErrorAction SilentlyContinue)
        if ($cmd) { $duarouter = $cmd.Source }
    }
    if (-not $duarouter) {
        Write-Error "duarouter not found. Set SUMO_HOME or add SUMO bin to PATH."
        exit 1
    }

    Write-Host "Rebuilding SUMO routes from Pathumwan/data/Dataset.csv ..." -ForegroundColor Cyan
    & $pythonCmd.Source $generator --dataset $destination --net $netFile --output-trips $outTrips --output-mapping $outMapping
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $duarouter -n $netFile --route-files $outTrips -o $outRou --remove-loops true --ignore-errors true --no-step-log true
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "Build complete (no pause)." -ForegroundColor Green
}
