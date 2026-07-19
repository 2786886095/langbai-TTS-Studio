param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [int]$ProcessId = 0
)

Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;

public static class LangbaiWindowCapture {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int command);
}
'@

$process = if ($ProcessId -gt 0) {
    Get-Process -Id $ProcessId -ErrorAction Stop
} else {
    Get-Process langbai-TTS-Studio -ErrorAction Stop |
        Where-Object { $_.MainWindowHandle -ne 0 } |
        Sort-Object StartTime -Descending |
        Select-Object -First 1
}

if (-not $process -or $process.MainWindowHandle -eq 0) {
    throw "No visible langbai TTS Studio window was found."
}

$handle = [IntPtr]$process.MainWindowHandle
[LangbaiWindowCapture]::ShowWindow($handle, 9) | Out-Null
[LangbaiWindowCapture]::SetForegroundWindow($handle) | Out-Null
Start-Sleep -Milliseconds 500

$rect = New-Object LangbaiWindowCapture+RECT
if (-not [LangbaiWindowCapture]::GetWindowRect($handle, [ref]$rect)) {
    throw "Unable to read the application window bounds."
}

$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
$bitmap = New-Object System.Drawing.Bitmap $width, $height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
    $resolved = [System.IO.Path]::GetFullPath($OutputPath)
    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($resolved)) | Out-Null
    $bitmap.Save($resolved, [System.Drawing.Imaging.ImageFormat]::Png)
    [PSCustomObject]@{
        Path = $resolved
        ProcessId = $process.Id
        Width = $width
        Height = $height
    }
} finally {
    $graphics.Dispose()
    $bitmap.Dispose()
}
