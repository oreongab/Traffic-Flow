param(
    [Parameter(Mandatory = $true)]
    [string]$Pack,

    [switch]$Build,

    # Copy only Pathumwan/data/Dataset.csv (do not touch derived files)
    [switch]$DatasetOnly
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$packsRoot = (Resolve-Path $PSScriptRoot).Path

$packRoot = Join-Path $packsRoot $Pack

$sourceDataset = [System.IO.Path]::Combine($packRoot, "Pathumwan", "data", "Dataset.csv")
if (-not (Test-Path $sourceDataset)) {
    Write-Error "Pack not found or missing Dataset.csv: $sourceDataset"
    Write-Host "Available packs:" -ForegroundColor Yellow
    Get-ChildItem -Path $packsRoot -Directory | Select-Object -ExpandProperty Name | Sort-Object | ForEach-Object { Write-Host "  - $_" }
    exit 1
}

$destination = [System.IO.Path]::Combine($repoRoot, "Pathumwan", "data", "Dataset.csv")
if (-not (Test-Path (Split-Path -Parent $destination))) {
    Write-Error "Destination folder not found: $(Split-Path -Parent $destination)"
    exit 1
}

Copy-Item -Path $sourceDataset -Destination $destination -Force
Write-Host "Copied: $sourceDataset" -ForegroundColor Green
Write-Host "   -> $destination" -ForegroundColor Green

$pathumwanRoot = [System.IO.Path]::Combine($repoRoot, "Pathumwan")

# Copy derived SUMO files if present (so SUMO changes immediately without rebuild)
if (-not $DatasetOnly) {
    $derivedPairs = @(
        @{
            Source = [System.IO.Path]::Combine($packRoot, "Pathumwan", "osm.dataset.trips.xml")
            Dest   = [System.IO.Path]::Combine($pathumwanRoot, "osm.dataset.trips.xml")
        },
        @{
            Source = [System.IO.Path]::Combine($packRoot, "Pathumwan", "osm.dataset.rou.xml.gz")
            Dest   = [System.IO.Path]::Combine($pathumwanRoot, "osm.dataset.rou.xml.gz")
        },
        @{
            Source = [System.IO.Path]::Combine($packRoot, "Pathumwan", "data", "dataset_route_mapping.generated.json")
            Dest   = [System.IO.Path]::Combine($pathumwanRoot, "data", "dataset_route_mapping.generated.json")
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
    $generator = [System.IO.Path]::Combine($pathumwanRoot, "generate_dataset_routes.py")
    $netFile = [System.IO.Path]::Combine($pathumwanRoot, "osm.net.xml")
    $outTrips = [System.IO.Path]::Combine($pathumwanRoot, "osm.dataset.trips.xml")
    $outRou = [System.IO.Path]::Combine($pathumwanRoot, "osm.dataset.rou.xml.gz")
    $outMapping = [System.IO.Path]::Combine($pathumwanRoot, "data", "dataset_route_mapping.generated.json")

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
        $candidate = [System.IO.Path]::Combine($env:SUMO_HOME, "bin", "duarouter.exe")
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

    & $duarouter -n $netFile --route-files $outTrips -o $outRou --alternatives-output NUL --remove-loops true --ignore-errors true --no-step-log true
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "Build complete (no pause)." -ForegroundColor Green
}
