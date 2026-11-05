# Development smoke test: same bundled CPython / Python.NET, standalone .NET
# Framework host. This does NOT test Revit 2025's .NET 8 process or model API.
# It does not install packages or change pyRevit settings.
param([ValidateSet('preview_winforms.py', 'check_shapely.py')][string]$TestScript = 'preview_winforms.py')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$engineRoot = 'C:\Program Files\pyRevit-Master\bin\cengines\CPY3123'
$bridge = 'C:\Program Files\pyRevit-Master\bin\netfx\pyRevitLabs.PythonNet.dll'
Add-Type -Path $bridge
[Python.Runtime.Runtime]::PythonDLL = Join-Path $engineRoot 'python312.dll'
[Python.Runtime.PythonEngine]::PythonHome = $engineRoot
[Python.Runtime.PythonEngine]::Initialize()
$gil = [Python.Runtime.Py]::GIL()
try {
    $testFile = Join-Path $PSScriptRoot $TestScript
    $pathLiteral = $testFile.Replace('\', '\\').Replace("'", "\'")
    [Python.Runtime.PythonEngine]::Exec("__file__ = '$pathLiteral'`nexec(compile(open('$pathLiteral', encoding='utf-8').read(), '$pathLiteral', 'exec'))")
} catch {
    if ($_.Exception.InnerException -is [Python.Runtime.PythonException]) {
        Write-Output $_.Exception.InnerException.Format()
    }
    throw
} finally {
    $gil.Dispose()
    # An isolated short-lived test process owns the runtime. No CPython shutdown
    # inside Revit, and no attempt to modify the active user's application.
}
