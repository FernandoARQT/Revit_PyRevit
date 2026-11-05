# Uses installed pyRevit's bridge in an isolated .NET Framework test process.
# Synthetic inputs only. No Revit process, document or installation is modified.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
Add-Type -AssemblyName System.Data
$engineRoot = 'C:\Program Files\pyRevit-Master\bin\cengines\CPY3123'
$bridge = 'C:\Program Files\pyRevit-Master\bin\netfx\pyRevitLabs.PythonNet.dll'
Add-Type -Path $bridge
[Python.Runtime.Runtime]::PythonDLL = Join-Path $engineRoot 'python312.dll'
[Python.Runtime.PythonEngine]::PythonHome = $engineRoot
[Python.Runtime.PythonEngine]::Initialize()
$gil = [Python.Runtime.Py]::GIL()
try {
    $testFile = Join-Path $PSScriptRoot 'preview_ventilacion.py'
    $literal = $testFile.Replace('\', '\\').Replace("'", "\'")
    [Python.Runtime.PythonEngine]::Exec("__file__ = '$literal'`nexec(compile(open('$literal', encoding='utf-8').read(), '$literal', 'exec'))")
} catch {
    if ($_.Exception.InnerException -is [Python.Runtime.PythonException]) {
        Write-Output $_.Exception.InnerException.Format()
    }
    throw
} finally {
    $gil.Dispose()
}
