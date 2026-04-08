param(
    [string]$EnvFile = ".env"
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFilePath = if ([System.IO.Path]::IsPathRooted($EnvFile)) {
    $EnvFile
} else {
    Join-Path $scriptDir $EnvFile
}
$envFile = [System.IO.Path]::GetFullPath($envFilePath)

if (-not (Test-Path $envFile)) {
    throw "Missing env file at $envFile"
}

$argsList = New-Object System.Collections.Generic.List[string]
$userInput = ""

foreach ($line in Get-Content -Path $envFile) {
    $trimmed = $line.Trim()
    if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
        continue
    }

    $separatorIndex = $line.IndexOf("=")
    if ($separatorIndex -lt 0) {
        continue
    }

    $key = $line.Substring(0, $separatorIndex).Trim()
    $value = $line.Substring($separatorIndex + 1).Trim()

    if (
        ($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))
    ) {
        $value = $value.Substring(1, $value.Length - 2)
    }

    if ($key -eq "user_input") {
        $userInput = $value
        continue
    }

    $argsList.Add("--$key")
    $argsList.Add($value)
}

$commandArgs = @("run", "python", "src/cli.py", $userInput) + $argsList.ToArray()
$displayArgs = @('run', 'python', 'src/cli.py', ('"' + $userInput + '"')) + $argsList.ToArray()

Write-Host "[*] Executing command: uv $($displayArgs -join ' ')"
Write-Host "[*] Env file: $envFile"
Write-Host "------------------------------------------------------------"

Push-Location $scriptDir
try {
    & uv @commandArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
